"""Fixed capabilities and strict, bounded invocation. No arbitrary bank methods."""

import asyncio
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from ..errors import AgentError, forbidden
from ..sensitive import contains_secret
from ..schemas import AnalysisResult
from ..dag.schemas import (ActionProposal, AnalysisInputs, BalanceInputs, BalanceResult, BOutcome,
    CashflowInputs, PreparedOperation, QuoteResult, RecipientInputs, RecipientResult, TransferInputs)
from .schemas import (CallContext, CashflowReport, CompareInputs, CompareReport, FactStatus, MandatesResult,
    MandateStatusInputs, ScopeGrant, TransactionsInputs, TransactionsResult, TransferStatusInputs)
from .schemas import TransferFactStatus, MandateFactStatus


ALLOWED = {'get_balance', 'list_transactions', 'resolve_recipient', 'quote_transfer', 'get_transfer_status',
    'list_mandates', 'get_mandate_status', 'analyze_transactions', 'simulate_cashflow', 'compare_scenarios',
    'propose_transfer', 'propose_mandate_cancellation', 'get_operation_status'}
ALIASES = {'bank.balance':'get_balance', 'bank.recipient':'resolve_recipient', 'bank.quote':'quote_transfer',
           'analytics.analysis':'analyze_transactions', 'cashflow.simulate':'simulate_cashflow'}


@dataclass(frozen=True)
class ToolSpec:
    tool_name: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    module: str
    capability: str
    adapter: object
    handler: object
    side_effects: bool = False
    timeout_seconds: float = 5
    max_attempts: int = 2
    version: str = '0.1.0'
    confirmation_required: bool = False


class ToolRegistry:
    def __init__(self, directory):
        self.directory = directory
        self._tools = {}

    def register(self, spec: ToolSpec):
        if spec.tool_name not in ALLOWED or spec.tool_name in self._tools:
            raise ValueError('unknown or duplicate capability')
        if (not 0 < spec.timeout_seconds <= 30 or not 1 <= spec.max_attempts <= 3
                or (spec.side_effects and spec.max_attempts != 1)):
            raise ValueError('invalid retry/timeout policy')
        if spec.input_schema.model_config.get('extra') != 'forbid' or spec.output_schema.model_config.get('extra') != 'forbid':
            raise ValueError('closed schemas required')
        self._tools[spec.tool_name] = spec

    def get(self, name):
        spec = self._tools.get(ALIASES.get(name, name))
        if spec is None:
            raise AgentError('UNKNOWN_TOOL', 422)
        return spec

    def catalog(self):
        return [dict(tool_name=s.tool_name, module=s.module, version=s.version, capability=s.capability,
            side_effects=s.side_effects, timeout_seconds=s.timeout_seconds, max_attempts=s.max_attempts,
            confirmation_required=s.confirmation_required,
            available=s.adapter.supports(s.tool_name) if hasattr(s.adapter,'supports') else getattr(s.adapter,'available',False),
            provenance=s.adapter.provenance,
            input_schema=s.input_schema.model_json_schema(), output_schema=s.output_schema.model_json_schema())
            for s in self._tools.values()]

    def _scope(self, ctx, inputs):
        accounts = self.directory.accounts(ctx.principal)
        if not accounts:
            raise forbidden()
        account = getattr(inputs, 'source_account_id', None)
        if account is not None and account not in accounts:
            raise forbidden()
        if hasattr(inputs, 'plan') and inputs.plan.dataset_id not in ctx.principal.dataset_ids:
            raise forbidden()
        if hasattr(inputs, 'scenarios'):
            for scenario in inputs.scenarios:
                self._scope(ctx, scenario)
        if isinstance(inputs, ActionProposal):
            if (inputs.plan_id != ctx.plan_id or inputs.plan_version != ctx.plan_version
                    or inputs.node_id != ctx.node_id or inputs.constraints != ctx.constraints):
                raise forbidden()
            self._scope(ctx, inputs.inputs)
            if ctx.constraints.read_only:
                raise AgentError('READ_ONLY', 409)
            if inputs.tool_name == 'execution.cancel_mandate':
                protected = self.directory.protected(ctx.principal) | set(ctx.constraints.protected_mandate_ids)
                if inputs.inputs.mandate_id in protected:
                    raise AgentError('PROTECTED_MANDATE', 409)
        if isinstance(inputs, PreparedOperation) and (inputs.plan_id != ctx.plan_id or
            inputs.plan_version != ctx.plan_version or inputs.node_id != ctx.node_id):
            raise forbidden()

    async def invoke(self, name, raw, ctx: CallContext):
        spec = self.get(name)
        try:
            inputs = spec.input_schema.model_validate(raw.model_dump(warnings=False) if isinstance(raw,BaseModel) else raw).model_copy(deep=True)
        except (ValidationError, TypeError, ValueError) as exc:
            raise AgentError('INVALID_TOOL_INPUT', 422) from exc
        self._scope(ctx, inputs)
        expected_action={'propose_transfer':'execution.transfer',
                         'propose_mandate_cancellation':'execution.cancel_mandate'}.get(spec.tool_name)
        if expected_action and inputs.tool_name!=expected_action:
            raise forbidden()
        if spec.confirmation_required and not ctx.scope_ref:
            raise AgentError('AUTH_REQUIRED', 409, next_action='代扣查询需先由 B 确认账户读取范围。')
        for attempt in range(spec.max_attempts):
            try:
                self._scope(ctx, inputs)
                raw_result = await asyncio.wait_for(spec.handler(ctx, inputs.model_copy(deep=True)), spec.timeout_seconds)
                payload=raw_result.model_dump(warnings=False) if isinstance(raw_result,BaseModel) else raw_result
                result = spec.output_schema.model_validate(payload).model_copy(deep=True)
                self._check_result(inputs, result, ctx)
                return result
            except (ValidationError, ValueError, TypeError) as exc:
                raise AgentError('INVALID_TOOL_RESULT', 502) from exc
            except AgentError as exc:
                code = exc.code if exc.code in {'UNSUPPORTED','FORBIDDEN','AUTH_REQUIRED','PROTECTED_MANDATE',
                    'READ_ONLY','UPSTREAM_UNAVAILABLE','PENDING_UNKNOWN'} else 'UPSTREAM_UNAVAILABLE'
                raise AgentError(code, exc.status, next_action='按当前工具状态处理；不自动执行银行操作。') from None
            except (asyncio.TimeoutError, httpx.HTTPError) as exc:
                transient = isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException, httpx.NetworkError))
                if isinstance(exc, httpx.HTTPStatusError):
                    transient = exc.response.status_code == 429 or exc.response.status_code >= 500
                if not transient or attempt + 1 == spec.max_attempts:
                    raise AgentError('UPSTREAM_UNAVAILABLE', 503) from None
            except Exception:
                raise AgentError('UPSTREAM_UNAVAILABLE', 503) from None

    def _check_result(self, inputs, result, ctx):
        if contains_secret(result.model_dump_json()):
            raise ValueError('sensitive tool result')
        if isinstance(result, TransactionsResult):
            if any(t.account_id != inputs.source_account_id for t in result.transactions):
                raise forbidden()
            if len(result.transactions)>inputs.limit or any(not inputs.start<=t.posted_on<inputs.end_exclusive for t in result.transactions):
                raise ValueError('transaction date/limit mismatch')
        if isinstance(result, MandatesResult):
            if any(m.account_id != inputs.source_account_id for m in result.mandates):
                raise forbidden()
        if isinstance(result, RecipientResult) and result.recipient_id != inputs.recipient_id:
            raise ValueError('recipient mismatch')
        if isinstance(result,FactStatus):
            expected = inputs.reference if isinstance(inputs,TransferStatusInputs) else inputs.mandate_id
            if result.reference!=expected:
                raise ValueError('bank fact reference mismatch')
        if isinstance(result, CashflowReport):
            a = result.assumptions
            if (a.source_account_id != inputs.source_account_id or a.target_amount_cents != inputs.amount_cents
                    or a.target_date != inputs.target_date or a.salary_date != inputs.salary_date or result.constraints != ctx.constraints):
                raise ValueError('cashflow request/constraints mismatch')
        if isinstance(result, CompareReport):
            if len(result.scenarios) != len(inputs.scenarios):
                raise ValueError('scenario count mismatch')
            for scenario, output in zip(inputs.scenarios, result.scenarios):
                self._check_result(scenario, output, ctx)
        if isinstance(result, PreparedOperation):
            for field in ('plan_id','plan_version','node_id'):
                if getattr(result,field) != getattr(ctx,field):
                    raise ValueError('B binding mismatch')
            if isinstance(inputs,PreparedOperation) and result.operation_id!=inputs.operation_id:
                raise ValueError('B operation mismatch')
        if isinstance(result, BOutcome):
            self._scope(ctx, result.parameters)


def build_registry(directory, bank, gate, analytics, *, timeout=5):
    registry = ToolRegistry(directory)

    def add(name, inp, out, module, adapter, method, *, effect=False, yellow=False, custom=None):
        async def call(ctx, inputs):
            if yellow:
                target=inputs.inputs if isinstance(inputs,ActionProposal) else inputs
                grant = ScopeGrant.model_validate(await gate.check_mandate_scope(ctx.principal, target, ctx.scope_ref))
                if (not grant.approved or grant.subject != ctx.principal.subject or
                        grant.source_account_id != target.source_account_id or grant.scope_ref != ctx.scope_ref):
                    raise forbidden()
            if custom:
                return await custom(ctx, inputs)
            return await getattr(adapter, method)(ctx.principal, inputs)
        registry.register(ToolSpec(name,inp,out,module,name,adapter,call,side_effects=effect,
            timeout_seconds=timeout,max_attempts=1 if effect else 2,confirmation_required=yellow))

    add('get_balance',BalanceInputs,BalanceResult,'A',bank,'get_balance')
    add('list_transactions',TransactionsInputs,TransactionsResult,'A',bank,'list_transactions')
    add('resolve_recipient',RecipientInputs,RecipientResult,'A',bank,'resolve_recipient')
    add('quote_transfer',TransferInputs,QuoteResult,'A',bank,'quote_transfer')
    add('get_transfer_status',TransferStatusInputs,TransferFactStatus,'A',bank,'get_transfer_status')
    add('list_mandates',BalanceInputs,MandatesResult,'A',bank,'list_mandates',yellow=True)
    add('get_mandate_status',MandateStatusInputs,MandateFactStatus,'A',bank,'get_mandate_status',yellow=True)
    add('analyze_transactions',AnalysisInputs,AnalysisResult,'D',analytics,'execute_analysis_plan')
    async def simulate(ctx, inputs):
        return await analytics.simulate_cashflow(ctx.principal, inputs, ctx.constraints.model_copy(deep=True))
    async def compare(ctx, inputs):
        return await analytics.compare_cashflow_scenarios(ctx.principal, inputs, ctx.constraints.model_copy(deep=True))
    add('simulate_cashflow',CashflowInputs,CashflowReport,'D',analytics,'simulate_cashflow',custom=simulate)
    add('compare_scenarios',CompareInputs,CompareReport,'D',analytics,'compare_cashflow_scenarios',custom=compare)
    add('propose_transfer',ActionProposal,PreparedOperation,'B',gate,'create_operation',effect=True)
    add('propose_mandate_cancellation',ActionProposal,PreparedOperation,'B',gate,'create_operation',effect=True,yellow=True)
    add('get_operation_status',PreparedOperation,BOutcome,'B',gate,'get_operation_status')
    return registry
