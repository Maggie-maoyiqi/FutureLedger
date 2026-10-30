"""Deterministic templates from validated C1 proposals, not model permissions."""

from ..errors import AgentError
from .schemas import GraphDefinition


class DagPlanner:
    @staticmethod
    def from_intent(result, directory, principal):
        if result.missing_fields or result.status in {'BLOCKED', 'CANCELLED'}:
            raise AgentError('NEEDS_INFO', 409, next_action='先完成 C1 补参及业务校验。')
        e = result.entities
        nodes = []

        def add(id, tool, inputs, deps=()):
            nodes.append(dict(node_id=id, tool_name=tool, inputs=inputs, depends_on=list(deps)))

        if result.intent == 'analysis':
            add('analysis', 'analytics.analysis', {'plan': e.analysis_plan.model_dump()})
        elif result.intent == 'transfer':
            params = {key: getattr(e, key) for key in ('source_account_id', 'recipient_id', 'amount_cents', 'execution_date')}
            add('balance', 'bank.balance', {'source_account_id': e.source_account_id})
            add('recipient', 'bank.recipient', {key: params[key] for key in ('source_account_id', 'recipient_id')})
            add('quote', 'bank.quote', params, ('balance', 'recipient'))
            if not result.constraints.read_only:
                add('payment', 'execution.transfer', params, ('quote',))
        elif result.intent == 'goal':
            add('balance', 'bank.balance', {'source_account_id': e.source_account_id})
            add('cashflow', 'cashflow.simulate', dict(source_account_id=e.source_account_id,
                amount_cents=e.amount_cents, target_date=e.target_date), ('balance',))
        else:
            matches = [m for m in directory.mandates(principal) if m['id'] == e.mandate_id]
            account = e.source_account_id or (matches[0]['account_id'] if matches else None)
            if not account:
                raise AgentError('NEEDS_INFO', 409, next_action='请明确代扣读取范围的来源账户。')
            inputs = dict(source_account_id=account, mandate_id=e.mandate_id)
            add('scope', 'execution.mandate_scope', inputs)
            if e.mandate_action == 'cancel' and not result.constraints.read_only:
                add('cancel', 'execution.cancel_mandate', inputs, ('scope',))
        return GraphDefinition(nodes=nodes)
