"""Server-side resolution and policy. Model output never authorizes anything."""

import asyncio
import json
import re
from datetime import date

import httpx
from pydantic import BaseModel, ValidationError

from demo.provider import ProviderError

from .adapters import Directory
from .auth import Principal
from .context import ContextStore, new_id
from .errors import AgentError, forbidden
from .planner import LocalPlanner, Planner, MINIMUM_EXPRESSION
from .money import yuan_to_cents
from .sensitive import reject_credentials, contains_secret
from .schemas import AgentIntent, ChatRequest, ChatResult, Constraints, Entities


READ_ONLY = re.compile(r'只看|只读|先不|不要转|别转|不转|不要执行|不要取消|仅查询|'
                       r'only\s+(?:preview|read)|do\s+not\s+(?:transfer|execute)|don.t\s+transfer', re.I)
REFER_BACK = re.compile(r'还是上次|上次那个|同上|沿用上次|和上次一样')
QUESTIONS = {'amount_cents': '请补充金额（整数分或明确元金额）。',
             'recipient_id': '请选择当前可见且无歧义的已验证收款人。',
             'source_account_id': '请选择有权使用的来源账户。',
             'execution_date': '请补充执行日期（YYYY-MM-DD）。',
             'analysis_plan': '请说明日期范围、类别及支出/退款/净支出指标；C1 支持单值查询。',
             'mandate_id': '请明确代扣授权；读取与取消仍需独立范围确认。',
             'mandate_action': '请说明查看还是提议取消代扣。',
             'goal_name': '请说明储蓄目标。', 'target_date': '请补充目标日期（YYYY-MM-DD）。'}


def entity_values(entities: Entities):
    # Preserve required nulls *inside* AnalysisPlan (notably group_by).
    return {key: value for key, value in entities.model_dump().items() if value is not None}


class AgentService:
    def __init__(self, directory: Directory, store: ContextStore, planner: Planner | None = None,
                 *, provider_timeout: float = 25):
        self.directory, self.store = directory, store
        self.planner = planner or LocalPlanner()
        self.provider_timeout = provider_timeout

    async def chat(self, request: ChatRequest, principal: Principal, today: date) -> ChatResult:
        reject_credentials(request.text)
        session = self.store.session(request.session_id, principal)
        async with session.lock:
            scope = self.store.scope(session, principal)
            accounts = self.directory.accounts(scope)
            if not accounts:
                raise forbidden()
            refs = list(dict.fromkeys(request.context_refs))
            if not refs and REFER_BACK.search(request.text):
                if not session.records:
                    raise AgentError('NEEDS_INFO', 409, next_action='本会话没有可复用的历史，请明确提供参数。')
                refs = [session.records[-1]]
            records = [self.store.reference(ref, session) for ref in refs]
            recipients = self.directory.recipients(scope)
            # No raw messages, credentials, SQL, transaction memos or other subjects.
            context = {'accounts': list(accounts),
                       'recipients': [{k: r[k] for k in ('id', 'name', 'alias', 'tail4')} for r in recipients],
                       'constraints': session.constraints.model_dump(),
                       'references': [dict(context_ref=r.result.context_ref, intent=r.result.intent,
                                           entities=entity_values(r.result.entities)) for r in records]}
            if contains_secret(json.dumps(context,ensure_ascii=False)):
                raise AgentError('SENSITIVE_CONTEXT',502,next_action='授权语义上下文需要先脱敏。')
            try:
                raw = await asyncio.wait_for(self.planner.plan(request.text, today, context), self.provider_timeout)
                # Validate native objects strictly; JSON strings are accepted only as a JSON object.
                parsed = AgentIntent.model_validate_json(raw) if isinstance(raw, str) else AgentIntent.model_validate(
                    raw.model_dump(warnings=False) if isinstance(raw,BaseModel) else raw)
            except (asyncio.TimeoutError, httpx.HTTPError, ProviderError) as exc:
                raise AgentError('UPSTREAM_UNAVAILABLE', 503, next_action='模型服务不可用；稍后重试，不执行操作。') from exc
            except (ValidationError, ValueError, TypeError) as exc:
                raise AgentError('INVALID_INTENT', 422 if isinstance(self.planner, LocalPlanner) else 502,
                                 next_action='结构化意图无效，请明确参数后重试。') from exc
            if set(parsed.context_refs) - set(refs):
                raise forbidden()
            # Request-selected references are permitted; model cannot select additional history.
            fields = {}
            for record in records:
                if record.result.intent != parsed.intent:
                    raise AgentError('INVALID_CONTEXT', 409, next_action='不同业务意图不能继承参数。')
                inherited = entity_values(record.result.entities)
                for missing in record.result.missing_fields:
                    inherited.pop(missing, None)
                # An ambiguous name is not a resolved recipient, even if it is in the history.
                if 'recipient_id' in record.result.missing_fields:
                    inherited.pop('recipient_name', None)
                for key, value in inherited.items():
                    if key in fields and fields[key] != value:
                        raise AgentError('NEEDS_INFO', 409, next_action='引用的历史参数冲突，请明确选择。')
                    fields[key] = value
            explicit = entity_values(parsed.entities)
            if 'recipient_name' in explicit and 'recipient_id' not in explicit:
                fields.pop('recipient_id', None)
            if 'recipient_id' in explicit and 'recipient_name' not in explicit:
                fields.pop('recipient_name', None)
            if 'mandate_name' in explicit and 'mandate_id' not in explicit:
                fields.pop('mandate_id', None)
            if 'mandate_id' in explicit and 'mandate_name' not in explicit:
                fields.pop('mandate_name', None)
            fields.update(explicit)
            entities = Entities.model_validate(fields)
            mandates = self.directory.mandates(scope)
            visible_mandates = {m['id'] for m in mandates}
            if set(parsed.constraints.protected_mandate_ids) - visible_mandates:
                raise forbidden()
            protected = (set(session.constraints.protected_mandate_ids)
                         | set(parsed.constraints.protected_mandate_ids) | self.directory.protected(scope))
            for mandate in mandates:
                if re.search(r'别动|不要动|保留|保护', request.text) and (
                        mandate['id'] in request.text or mandate['merchant'].removeprefix('模拟') in request.text):
                    protected.add(mandate['id'])
            min_values = [v for v in (session.constraints.min_balance_cents,
                                      parsed.constraints.min_balance_cents) if v is not None]
            for match in MINIMUM_EXPRESSION.finditer(request.text):
                min_values.append(yuan_to_cents(match[1], allow_zero=True))
            constraints = Constraints(read_only=session.constraints.read_only or parsed.constraints.read_only
                                      or bool(READ_ONLY.search(request.text)),
                                      protected_mandate_ids=sorted(protected),
                                      min_balance_cents=max(min_values) if min_values else None)
            missing, candidates = self.resolve(parsed.intent, entities, scope, today, recipients, mandates)
            action = {'analysis': 'analyze', 'transfer': 'propose_transfer', 'goal': 'simulate_goal',
                      'mandate': 'propose_mandate_cancel' if entities.mandate_action == 'cancel'
                      else 'request_mandate_scope'}[parsed.intent]
            blocked = (parsed.intent == 'mandate' and entities.mandate_action == 'cancel'
                       and entities.mandate_id in protected)
            status = ('BLOCKED' if blocked else 'NEEDS_INFO' if missing else
                      'READ_ONLY' if constraints.read_only else
                      'AWAITING_SCOPE_CONFIRMATION' if action == 'request_mandate_scope' else 'PROPOSED')
            result = ChatResult(task_id=new_id('task'), plan_id=new_id('plan'), plan_version=1,
                                context_ref=new_id('context'), intent=parsed.intent, status=status,
                                entities=entities, constraints=constraints, missing_fields=missing,
                                questions=[QUESTIONS[f] for f in missing], recipient_candidates=candidates,
                                proposed_steps=[{'action': action}])
            self.store.save(session, result)
            return result

    def resolve(self, kind, entities, scope, today, recipients, mandates):
        missing, candidates = [], []
        if entities.source_account_id is not None and entities.source_account_id not in self.directory.accounts(scope):
            raise forbidden()
        for when in (entities.execution_date, entities.target_date):
            if when is not None and date.fromisoformat(when) < today:
                raise AgentError('INVALID_DATE', 422, next_action='执行或目标日期不能早于上海业务日。')
        if kind == 'analysis':
            if entities.analysis_plan is None:
                missing.append('analysis_plan')
        elif kind == 'transfer':
            matches = []
            if entities.recipient_id:
                matches = [r for r in recipients if r['id'] == entities.recipient_id]
                if not matches:
                    raise forbidden()
                if entities.recipient_name and entities.recipient_name not in (matches[0]['name'], matches[0]['alias']):
                    raise AgentError('NEEDS_INFO', 409, next_action='收款人名称与标识不一致，请重新选择。')
            elif entities.recipient_name:
                matches = [r for r in recipients if entities.recipient_name in (r['name'], r['alias'])]
            verified = [r for r in matches if r['verified']]
            if len(matches) == 1 and len(verified) == 1:
                entities.recipient_id = verified[0]['id']
                entities.recipient_name = verified[0]['name']
            else:
                entities.recipient_id = None
                candidates = [{k: r[k] for k in ('id', 'name', 'alias', 'tail4')} for r in verified]
            missing = [f for f in ('amount_cents', 'recipient_id', 'source_account_id', 'execution_date')
                       if getattr(entities, f) is None]
        elif kind == 'goal':
            missing = [f for f in ('goal_name', 'amount_cents', 'source_account_id', 'target_date')
                       if getattr(entities, f) is None]
        else:
            if entities.mandate_id:
                matches = [m for m in mandates if m['id'] == entities.mandate_id]
                if not matches:
                    raise forbidden()
                if entities.mandate_name and entities.mandate_name not in (
                        matches[0]['merchant'], matches[0]['merchant'].removeprefix('模拟')):
                    raise AgentError('NEEDS_INFO', 409, next_action='代扣名称与标识不一致，请重新选择。')
            else:
                matches = [m for m in mandates if entities.mandate_name
                           and entities.mandate_name in (m['merchant'], m['merchant'].removeprefix('模拟'))]
            if len(matches) == 1:
                entities.mandate_id = matches[0]['id']
            if entities.mandate_action is None:
                missing.append('mandate_action')
            if entities.mandate_action == 'cancel' and entities.mandate_id is None:
                missing.append('mandate_id')
        return missing, candidates

    def operation_proposal(self, principal, plan_id, version, action, today):
        record = self.store.plan(plan_id, principal, version)
        session = self.store.session(record.session_id, principal)
        result = record.result
        if session.constraints.read_only or result.constraints.read_only:
            raise AgentError('READ_ONLY', 409, next_action='当前会话仅预览；不能创建写操作。')
        if result.entities.mandate_id in session.constraints.protected_mandate_ids:
            raise AgentError('PROTECTED_MANDATE', 409, next_action='受保护代扣不能创建取消操作。')
        if result.missing_fields:
            raise AgentError('NEEDS_INFO', 409, next_action='先补齐参数并消除歧义。')
        if action not in [s.action for s in result.proposed_steps] or result.intent not in {'transfer', 'mandate'}:
            raise AgentError('INVALID_ACTION', 422)
        scope = self.store.scope(session, principal)
        missing, _ = self.resolve(result.intent, result.entities.model_copy(deep=True), scope,
                                  today, self.directory.recipients(scope), self.directory.mandates(scope))
        if missing:
            raise AgentError('NEEDS_INFO', 409, next_action='目录状态已变更，请重新解析提议。')
        return record
