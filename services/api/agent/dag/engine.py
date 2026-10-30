"""Scoped, serialized DAG orchestration and incremental version transitions.

No method in this engine submits a bank write or handles confirmation secrets.
Financial success can only come from the configured B observation boundary.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from ..context import new_id
from ..errors import AgentError, forbidden
from ..schemas import AnalysisResult
from .graph import ancestors, descendants, topological_order
from .planner import DagPlanner
from .ports import UnavailableBankExecution, UnavailableReads
from .schemas import (ActionProposal, BalanceResult, BOutcome, CashflowResult, DagCreate, DagPlan,
    GraphDefinition, InputChange, InvalidationReceipt, NodeState, PreparedOperation, QuoteResult,
    RecipientResult, ReplanRequest, ResultChange, ResultRecord, VersionDiff, WRITE_TOOLS)


READ_RESULTS = {'bank.balance': BalanceResult, 'bank.recipient': RecipientResult, 'bank.quote': QuoteResult,
                'analytics.analysis': AnalysisResult, 'cashflow.simulate': CashflowResult}


@dataclass
class History:
    subject: str
    versions: list[DagPlan]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DagEngine:
    def __init__(self, store, directory, *, tools=None, bank_execution=None, clock=None, timeout=25):
        self.store, self.directory = store, directory
        self.tools = tools or UnavailableReads()
        self.b = bank_execution or UnavailableBankExecution()
        self.clock = clock or (lambda: datetime.now(ZoneInfo('Asia/Shanghai')).date())
        self.timeout = timeout
        self.histories: dict[str, History] = {}
        self.results: dict[str, ResultRecord] = {}
        self.bindings: dict[str, tuple[str, PreparedOperation]] = {}
        self.promoted: dict[str, str] = {}
        self.legacy_inflight: dict[str, int] = {}
        self.legacy_unresolved: set[str] = set()
        self.pending_changed: dict[str, set[str]] = {}
        self.event_sink = None
        self.access_guard = None
        self.observation_guard = None

    def _notify(self, kind, plan, node=None):
        if self.event_sink:
            self.event_sink(kind, plan, node)

    def _access(self, id, principal, version=None, *, allow_constraints=False, allow_cancelled=False):
        history = self.histories.get(id)
        if history is None or history.subject != principal.subject:
            raise forbidden()
        plan = history.versions[-1]
        if self.access_guard and not allow_cancelled:
            self.access_guard(id)
        session = self.store.session(plan.session_id, principal)
        scope = self.store.scope(session, principal)
        if plan.origin_c1_plan_id:
            record = self.store.records[self.store.plans[plan.origin_c1_plan_id]]
            if record.result.status == 'CANCELLED' and not allow_cancelled:
                raise AgentError('PLAN_STALE', 409)
        if version is not None and version != plan.plan_version:
            raise AgentError('PLAN_STALE', 409, next_action='请读取当前 DAG 版本，旧版本不再调度。')
        if not allow_constraints and (plan.constraints != session.constraints or plan.state == 'REQUIRES_REPLAN'):
            raise AgentError('PLAN_STALE', 409, next_action='业务约束或上游事实已改变，需要重规划。')
        return history, plan, session, scope

    def _validate(self, nodes, constraints, scope):
        accounts = self.directory.accounts(scope)
        recipients = {r['id']: r for r in self.directory.recipients(scope)}
        mandates = {m['id']: m for m in self.directory.mandates(scope)}
        protected = set(constraints.protected_mandate_ids) | self.directory.protected(scope)
        if not accounts:
            raise forbidden()
        for node in nodes:
            if isinstance(node, NodeState) and node.status == 'SUCCEEDED' and node.tool_name in WRITE_TOOLS:
                continue  # Immutable historical fact, not a new operation/date validation.
            inputs = node.inputs
            if hasattr(inputs, 'source_account_id') and inputs.source_account_id not in accounts:
                raise forbidden()
            if hasattr(inputs, 'plan') and inputs.plan.dataset_id not in scope.dataset_ids:
                raise forbidden()
            if hasattr(inputs, 'recipient_id'):
                recipient = recipients.get(inputs.recipient_id)
                if not recipient or not recipient['verified']:
                    raise forbidden()
            if getattr(inputs, 'mandate_id', None):
                mandate = mandates.get(inputs.mandate_id)
                if not mandate or mandate['account_id'] != inputs.source_account_id:
                    raise forbidden()
                if node.tool_name == 'execution.cancel_mandate' and inputs.mandate_id in protected:
                    raise AgentError('PROTECTED_MANDATE', 409)
            for key in ('execution_date', 'target_date', 'salary_date'):
                when = getattr(inputs, key, None)
                if when and date.fromisoformat(when) < self.clock():
                    raise AgentError('INVALID_DATE', 422)
            if node.tool_name in WRITE_TOOLS and constraints.read_only:
                raise AgentError('READ_ONLY', 409)

    def create(self, request: DagCreate, principal, *, origin=None):
        session = self.store.session(request.session_id, principal)
        scope = self.store.scope(session, principal)
        self._validate(request.nodes, session.constraints, scope)
        if len(self.histories) >= 1000:
            raise AgentError('CAPACITY_EXCEEDED', 503)
        nodes = [NodeState(**n.model_dump(), risk_level='B_REQUIRED' if n.tool_name.startswith('execution.') else 'READ_ONLY',
                    compensation='MANUAL_REVIEW_ONLY' if n.tool_name in WRITE_TOOLS else 'NONE') for n in request.nodes]
        plan = DagPlan(plan_id=new_id('dag'), session_id=session.id, plan_version=1, nodes=nodes,
                       constraints=session.constraints.model_copy(deep=True), diff=VersionDiff(reason='initial'),
                       origin_c1_plan_id=origin)
        self.histories[plan.plan_id] = History(principal.subject, [plan])
        if origin:
            self.promoted[origin] = plan.plan_id
        self._emit(plan, principal.subject)
        self._notify('plan_created', plan)
        return plan.model_copy(deep=True)

    def from_c1(self, id, version, principal):
        record = self.store.plan(id, principal, version)
        if self.legacy_inflight.get(id, 0) or id in self.legacy_unresolved:
            raise AgentError('LEGACY_OPERATIONS_EXIST', 409,
                next_action='旧 C1 操作已创建或结果未明，当前协议不能使其凭证失效；不能提升为 DAG。')
        if id in self.promoted:
            return self.get(self.promoted[id], principal)
        session = self.store.session(record.session_id, principal)
        scope = self.store.scope(session, principal)
        graph = DagPlanner.from_intent(record.result, self.directory, scope)
        return self.create(DagCreate(session_id=session.id, nodes=graph.nodes), principal, origin=id)

    def guard_c1_plan(self, id):
        if id in self.promoted:
            raise AgentError('PLAN_STALE', 409, next_action='此提议已提升为 DAG，请使用 DAG 当前版本接口。')

    def begin_legacy_proposal(self, id):
        self.guard_c1_plan(id)
        self.legacy_inflight[id] = self.legacy_inflight.get(id, 0) + 1

    def finish_legacy_proposal(self, id, *, unresolved):
        self.legacy_inflight[id] -= 1
        if unresolved:
            self.legacy_unresolved.add(id)

    def guard_legacy_operation(self, principal, operation_id):
        entry = self.bindings.get(operation_id)
        if entry:
            plan_id, binding = entry
            self._access(plan_id, principal, binding.plan_version)
            raise AgentError('UNSUPPORTED', 501, next_action='DAG 操作确认和执行须由 B 的版本绑定接口处理。')

    def get(self, id, principal, version=None):
        history, current, _, _ = self._access(id, principal, allow_constraints=True, allow_cancelled=True)
        if version is None:
            return current.model_copy(deep=True)
        if not 1 <= version <= len(history.versions):
            raise AgentError('PLAN_STALE', 409)
        return history.versions[version - 1].model_copy(deep=True)

    def result(self, id, ref, principal):
        history, _, _, _ = self._access(id, principal, allow_constraints=True, allow_cancelled=True)
        if not any(n.result_ref == ref for p in history.versions for n in p.nodes):
            raise forbidden()
        return self.results[ref].model_copy(deep=True)

    def _emit(self, plan, subject):
        self.store.events.append(dict(id=len(self.store.events) + 1, subject=subject,
            data=dict(plan_id=plan.plan_id, plan_version=plan.plan_version, status=plan.state,
                      node_statuses={n.node_id: n.status for n in plan.nodes})))

    def _record(self, plan, node, data, source):
        ref = new_id('result')
        result = ResultRecord(result_ref=ref, node_id=node.node_id, tool_name=node.tool_name,
                              origin_plan_version=plan.plan_version, source=source, data=data)
        self.results[ref] = result.model_copy(deep=True)
        node.result_ref = ref
        return result

    def _conditions_met(self, plan, node):
        by_id = {n.node_id: n for n in plan.nodes}
        if any(by_id[id].status != 'SUCCEEDED' for id in node.depends_on):
            return False
        for condition in node.preconditions:
            parent = by_id[condition.node_id]
            if not parent.result_ref:
                return False
            value = getattr(self.results[parent.result_ref].data, condition.field)
            if (condition.op == 'eq' and value != condition.value) or (condition.op == 'gte' and value < condition.value):
                return False
        return True

    async def run(self, id, version, principal):
        history, _, _, _ = self._access(id, principal, version)
        async with history.lock:
            _, plan, session, scope = self._access(id, principal, version)
            async with session.lock:
                _, plan, _, scope = self._access(id, principal, version)
                self._validate(plan.nodes, plan.constraints, scope)
                by_id = {n.node_id: n for n in plan.nodes}
                for node_id in topological_order(plan.nodes):
                    self._access(id, principal, version)
                    node = by_id[node_id]
                    if node.status in {'SUCCEEDED', 'FAILED'}:
                        continue
                    if not self._conditions_met(plan, node):
                        node.status, node.error_code = 'BLOCKED', 'PRECONDITION_FAILED'
                        self._notify('node_failed', plan, node)
                        continue
                    if node.tool_name.startswith('execution.'):
                        if node.status == 'PENDING_UNKNOWN' and not node.operation_id:
                            continue  # Only B lookup may resolve a lost prepare response.
                        if node.tool_name not in WRITE_TOOLS or not node.operation_id:
                            node.status = 'WAITING_B'
                            continue
                        await self._observe(plan, node, scope)
                        continue
                    node.status, node.error_code = 'RUNNING', None
                    self._notify('node_started', plan, node)
                    dependencies = {dep: self.results[by_id[dep].result_ref].model_copy(deep=True)
                                    for dep in ancestors(plan.nodes, node_id) if by_id[dep].result_ref}
                    try:
                        if hasattr(self.tools, 'read_node'):
                            call = self.tools.read_node(plan.model_copy(deep=True), node.model_copy(deep=True), scope, dependencies)
                        else:
                            call = self.tools.read(node.tool_name, scope, node.inputs.model_copy(deep=True), dependencies)
                        raw = await asyncio.wait_for(call, self.timeout)
                        data = READ_RESULTS[node.tool_name].model_validate(raw)
                        if node.tool_name == 'bank.recipient' and data.recipient_id != node.inputs.recipient_id:
                            raise ValueError('recipient mismatch')
                        self._record(plan, node, data, self.tools.source(node.tool_name))
                        node.status = 'SUCCEEDED'
                        self._notify('node_completed', plan, node)
                    except (ValidationError, ValueError, TypeError):
                        node.status, node.error_code = 'FAILED', 'INVALID_TOOL_RESULT'
                        self._notify('node_failed', plan, node)
                        continue
                    except (AgentError, asyncio.TimeoutError, httpx.HTTPError) as exc:
                        node.status, node.error_code = 'FAILED', exc.code if isinstance(exc, AgentError) else 'UPSTREAM_UNAVAILABLE'
                        self._notify('node_failed', plan, node)
                        continue
                    except asyncio.CancelledError:
                        node.status, node.error_code = 'PENDING', 'INTERRUPTED'
                        raise
                    if any(getattr(data, e.field) != e.value for e in node.expected_result):
                        plan.state = 'REQUIRES_REPLAN'
                        self.pending_changed.setdefault(id, set()).add(node.node_id)
                        if any(n.status == 'PENDING_UNKNOWN' for n in plan.nodes):
                            self._emit(plan, principal.subject)
                            return plan.model_copy(deep=True)
                        return await self._revise(history, plan, scope, session.constraints,
                            {}, 'upstream_changed', forced={node.node_id}, seed={node.node_id})
                self._emit(plan, principal.subject)
                return plan.model_copy(deep=True)

    async def reconcile(self, id, version, principal):
        """Read B only, even while ordinary scheduling is blocked by stale facts."""
        history, _, _, _ = self._access(id, principal, version, allow_constraints=True, allow_cancelled=True)
        async with history.lock:
            _, plan, session, scope = self._access(id, principal, version, allow_constraints=True, allow_cancelled=True)
            async with session.lock:
                _, plan, _, scope = self._access(id, principal, version, allow_constraints=True, allow_cancelled=True)
                for node in plan.nodes:
                    if node.tool_name not in WRITE_TOOLS or node.status not in {'WAITING_B', 'PENDING_UNKNOWN'}:
                        continue
                    if not node.operation_id and node.status == 'PENDING_UNKNOWN':
                        lookup = getattr(self.b, 'lookup', None)
                        if lookup is None:
                            node.error_code = 'UNSUPPORTED'
                            continue
                        proposal = ActionProposal(plan_id=id, plan_version=version, node_id=node.node_id,
                            tool_name=node.tool_name, inputs=node.inputs.model_copy(deep=True),
                            constraints=plan.constraints.model_copy(deep=True))
                        try:
                            raw = await asyncio.wait_for(lookup(scope, proposal), self.timeout)
                            binding = PreparedOperation.model_validate(raw)
                            if (binding.plan_id != id or binding.plan_version != version or binding.node_id != node.node_id
                                    or binding.operation_id in self.bindings):
                                raise ValueError('lookup binding mismatch')
                            self.bindings[binding.operation_id] = (id, binding)
                            node.operation_id = binding.operation_id
                        except (AgentError, ValidationError, ValueError, TypeError, asyncio.TimeoutError, httpx.HTTPError):
                            node.error_code = 'PENDING_UNKNOWN'
                            continue
                    if node.operation_id:
                        await self._observe(plan, node, scope)
                roots = self.pending_changed.get(id, set())
                cancelled = (plan.origin_c1_plan_id and self.store.records[
                    self.store.plans[plan.origin_c1_plan_id]].result.status == 'CANCELLED')
                if roots and not cancelled and not any(n.status == 'PENDING_UNKNOWN' for n in plan.nodes):
                    return await self._revise(history, plan, scope, session.constraints, {},
                        'upstream_changed', forced=roots, seed=roots)
                self._emit(plan, principal.subject)
                return plan.model_copy(deep=True)

    async def _observe(self, plan, node, scope):
        _, binding = self.bindings[node.operation_id]
        if self.observation_guard and not self.observation_guard(binding):
            return
        try:
            raw = await asyncio.wait_for(self.b.observe(scope, binding.model_copy(deep=True)), self.timeout)
            data = BOutcome.model_validate(raw)
            if (any(getattr(data, key) != getattr(binding, key) for key in PreparedOperation.model_fields)
                    or data.parameters != node.inputs):
                raise ValueError('B binding/parameters mismatch')
            self._record(plan, node, data, self.b.source)
            node.error_code = None
            node.status = ('SUCCEEDED' if data.status == 'SUCCEEDED' else 'FAILED' if data.status == 'FAILED'
                           else 'PENDING_UNKNOWN' if data.status in {'SUBMITTING', 'PENDING_UNKNOWN'} else 'WAITING_B')
        except (ValidationError, ValueError, TypeError):
            node.status, node.error_code = 'PENDING_UNKNOWN', 'INVALID_B_RESULT'
        except (AgentError, asyncio.TimeoutError, httpx.HTTPError):
            node.status, node.error_code = 'PENDING_UNKNOWN', 'UPSTREAM_UNAVAILABLE'
        except asyncio.CancelledError:
            node.status, node.error_code = 'PENDING_UNKNOWN', 'INTERRUPTED'
            raise
        self._notify('operation_status_changed', plan, node)
        if node.status == 'SUCCEEDED':
            self._notify('node_completed', plan, node)
        elif node.status == 'FAILED':
            self._notify('node_failed', plan, node)
        elif node.result_ref and self.results[node.result_ref].data.status == 'NEEDS_CONFIRM':
            self._notify('confirmation_required', plan, node)

    def _financial_checks(self, plan, node):
        if not self._conditions_met(plan, node):
            raise AgentError('PRECONDITION_FAILED', 409)
        if node.tool_name == 'execution.cancel_mandate':
            # Scope-confirmation support must be provided by B, not invented by C.
            raise AgentError('UNSUPPORTED', 501, next_action='代扣范围确认尚未接入正式 B 协议。')
        upstream = [n for n in plan.nodes if n.node_id in ancestors(plan.nodes, node.node_id) and n.status == 'SUCCEEDED']
        balances = [n for n in upstream if n.tool_name == 'bank.balance' and n.inputs.source_account_id == node.inputs.source_account_id]
        recipients = [n for n in upstream if n.tool_name == 'bank.recipient'
                      and n.inputs.source_account_id == node.inputs.source_account_id and n.inputs.recipient_id == node.inputs.recipient_id]
        quotes = [n for n in upstream if n.tool_name == 'bank.quote' and n.inputs == node.inputs]
        if not balances or not recipients or not quotes:
            raise AgentError('PRECONDITION_FAILED', 409, next_action='提议前需要匹配参数的 A 余额、收款人及报价结果。')
        if not all(self.results[n.result_ref].data.verified for n in recipients):
            raise AgentError('PRECONDITION_FAILED', 409)
        if not all(self.results[n.result_ref].data.valid for n in quotes):
            raise AgentError('PRECONDITION_FAILED', 409)
        fee = max(self.results[n.result_ref].data.fee_cents for n in quotes)
        available = min(self.results[n.result_ref].data.available_cents for n in balances)
        if available < node.inputs.amount_cents + fee + (plan.constraints.min_balance_cents or 0):
            raise AgentError('INSUFFICIENT_FUNDS', 409)

    async def prepare(self, id, version, node_id, principal):
        history, _, _, _ = self._access(id, principal, version)
        async with history.lock:
            _, plan, session, scope = self._access(id, principal, version)
            async with session.lock:
                _, plan, _, scope = self._access(id, principal, version)
                self._validate(plan.nodes, plan.constraints, scope)
                node = next((n for n in plan.nodes if n.node_id == node_id), None)
                if node is None or node.tool_name not in WRITE_TOOLS:
                    raise AgentError('INVALID_ACTION', 422)
                if node.status == 'SUCCEEDED':
                    raise AgentError('OPERATION_ALREADY_COMPLETED', 409)
                if node.status == 'PENDING_UNKNOWN':
                    raise AgentError('PENDING_UNKNOWN', 409)
                if node.operation_id:
                    return self.bindings[node.operation_id][1].model_copy(deep=True)
                self._financial_checks(plan, node)
                proposal = ActionProposal(plan_id=id, plan_version=version, node_id=node_id,
                    tool_name=node.tool_name, inputs=node.inputs.model_copy(deep=True), constraints=plan.constraints.model_copy(deep=True))
                node.status = 'PENDING_UNKNOWN'  # An interrupted remote prepare must not be blindly retried.
                try:
                    raw = await asyncio.wait_for(self.b.prepare(scope, proposal), self.timeout)
                    binding = PreparedOperation.model_validate(raw)
                    if (binding.plan_id != id or binding.plan_version != version or binding.node_id != node_id
                            or binding.operation_id in self.bindings):
                        raise ValueError('B prepare binding mismatch')
                except AgentError as exc:
                    if exc.code == 'UNSUPPORTED':
                        node.status = 'WAITING_B'
                    raise
                except (ValidationError, ValueError, TypeError, asyncio.TimeoutError, httpx.HTTPError) as exc:
                    raise AgentError('PENDING_UNKNOWN', 409, next_action='B 提议结果未确定，需由 B 查证，不重复创建。') from exc
                self.bindings[binding.operation_id] = (id, binding)
                node.operation_id, node.status = binding.operation_id, 'WAITING_B'
                self._emit(plan, principal.subject)
                return binding.model_copy(deep=True)

    async def replan(self, id, request: ReplanRequest, principal):
        history, _, _, _ = self._access(id, principal, request.plan_version, allow_constraints=True)
        async with history.lock:
            _, plan, session, scope = self._access(id, principal, request.plan_version, allow_constraints=True)
            async with session.lock:
                _, plan, _, scope = self._access(id, principal, request.plan_version, allow_constraints=True)
                updates = request.updates.model_dump(exclude_unset=True)
                roots = self.pending_changed.get(id, set())
                return await self._revise(history, plan, scope, session.constraints, updates, request.reason,
                    forced=roots, seed=roots)

    async def _revise(self, history, old, scope, constraints, updates, reason, *, forced=None, seed=None):
        if len(history.versions) >= 100:
            raise AgentError('CAPACITY_EXCEEDED', 409)
        if any(n.status == 'PENDING_UNKNOWN' for n in old.nodes):
            raise AgentError('PENDING_UNKNOWN', 409, next_action='先由 B 查证提交状态，不能切换版本或再次付款。')
        new = old.model_copy(deep=True)
        new.plan_version += 1
        new.state = 'ACTIVE'
        new.constraints = constraints.model_copy(deep=True)
        changed, changes, conflicts = set(forced or ()), [], []
        for node in new.nodes:
            applicable = {k: v for k, v in updates.items() if k in type(node.inputs).model_fields}
            candidate = type(node.inputs).model_validate({**node.inputs.model_dump(), **applicable})
            if candidate != node.inputs:
                changed.add(node.node_id)
                completed = node.status == 'SUCCEEDED' and node.tool_name in WRITE_TOOLS
                changes.append(InputChange(node_id=node.node_id, before=node.inputs.model_copy(deep=True),
                                            after=candidate, applied=not completed))
                if completed:
                    conflicts.append('COMPLETED_ACTION_IMMUTABLE:' + node.node_id)
                else:
                    node.inputs = candidate
        if constraints != old.constraints:
            changed.update(n.node_id for n in new.nodes if n.tool_name in WRITE_TOOLS or n.tool_name == 'cashflow.simulate')
        if not changed:
            raise AgentError('NO_PLAN_CHANGE', 409)
        affected = descendants(new.nodes, changed)
        for node in new.nodes:
            if node.status == 'SUCCEEDED' and node.tool_name in WRITE_TOOLS:
                continue
            if node.node_id in affected and node.node_id not in (seed or set()):
                node.status, node.result_ref, node.error_code = 'PENDING', None, None
            if node.operation_id:
                node.operation_id, node.status, node.result_ref = None, 'PENDING', None
        self._validate(new.nodes, constraints, scope)
        operation_ids = [n.operation_id for n in old.nodes if n.operation_id and n.status != 'SUCCEEDED']
        if operation_ids:
            try:
                raw = await asyncio.wait_for(self.b.invalidate(scope, old.plan_id, old.plan_version, operation_ids), self.timeout)
                receipt = InvalidationReceipt.model_validate(raw)
                if (receipt.plan_id != old.plan_id or receipt.plan_version != old.plan_version
                        or sorted(receipt.operation_ids) != sorted(operation_ids)):
                    raise ValueError('invalidation mismatch')
            except (ValidationError, ValueError, TypeError, asyncio.TimeoutError, httpx.HTTPError) as exc:
                raise AgentError('UPSTREAM_UNAVAILABLE', 503, next_action='B 未确认旧版本凭证失效，不发布新版本。') from exc
        result_changes = []
        for node in old.nodes:
            if node.node_id in (seed or set()) and node.result_ref:
                data = self.results[node.result_ref].data
                for expected in node.expected_result:
                    actual = getattr(data, expected.field)
                    if actual != expected.value:
                        result_changes.append(ResultChange(node_id=node.node_id, field=expected.field,
                            expected=expected.value, actual=actual, result_ref=node.result_ref))
        new.diff = VersionDiff(reason=reason, previous_version=old.plan_version, changes=changes,
            affected_nodes=sorted(affected), retained_nodes=[n.node_id for n in new.nodes if n.status == 'SUCCEEDED'],
            conflicts=conflicts, result_changes=result_changes)
        old.state = 'SUPERSEDED'
        history.versions.append(new)
        self.pending_changed.pop(new.plan_id, None)
        self._emit(new, history.subject)
        self._notify('plan_replanned', new)
        return new.model_copy(deep=True)
