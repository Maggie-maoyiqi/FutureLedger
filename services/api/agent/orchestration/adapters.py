"""Replaceable A/B/D ports. Unavailable defaults never invent bank facts."""

from typing import Protocol

from ..errors import unsupported
from ..adapters import UnavailableAnalysis


class BankReadAdapter(Protocol):
    provenance: str
    async def get_balance(self, principal, inputs): ...
    async def list_transactions(self, principal, inputs): ...
    async def resolve_recipient(self, principal, inputs): ...
    async def quote_transfer(self, principal, inputs): ...
    async def get_transfer_status(self, principal, inputs): ...
    async def list_mandates(self, principal, inputs): ...
    async def get_mandate_status(self, principal, inputs): ...


class ExecutionGateAdapter(Protocol):
    provenance: str
    async def create_operation(self, principal, proposal): ...
    async def confirm_operation(self, principal, binding, command): ...
    async def request_verification(self, principal, binding, command): ...
    async def execute_operation(self, principal, binding, command, idempotency_key): ...
    async def get_operation_status(self, principal, binding): ...
    async def cancel_unsubmitted_task(self, principal, binding): ...
    async def lookup_operation(self, principal, proposal): ...
    async def invalidate_operations(self, principal, plan_id, version, operation_ids): ...
    async def check_mandate_scope(self, principal, inputs, scope_ref): ...


class AnalyticsAdapter(Protocol):
    provenance: str
    async def execute_analysis_plan(self, principal, inputs): ...
    async def simulate_cashflow(self, principal, inputs, constraints): ...
    async def compare_cashflow_scenarios(self, principal, inputs, constraints): ...


class UnavailableBankRead:
    provenance = 'A'
    available = False

    async def get_balance(self, *args): raise unsupported('A get_balance')
    async def list_transactions(self, *args): raise unsupported('A list_transactions')
    async def resolve_recipient(self, *args): raise unsupported('A resolve_recipient')
    async def quote_transfer(self, *args): raise unsupported('A quote_transfer')
    async def get_transfer_status(self, *args): raise unsupported('A get_transfer_status')
    async def list_mandates(self, *args): raise unsupported('A list_mandates')
    async def get_mandate_status(self, *args): raise unsupported('A get_mandate_status')


class UnavailableGate:
    provenance = 'B'
    available = False

    async def create_operation(self, *args): raise unsupported('B create_operation')
    async def confirm_operation(self, *args): raise unsupported('B confirm_operation')
    async def request_verification(self, *args): raise unsupported('B request_verification')
    async def execute_operation(self, *args): raise unsupported('B execute_operation')
    async def get_operation_status(self, *args): raise unsupported('B get_operation_status')
    async def cancel_unsubmitted_task(self, *args): raise unsupported('B cancel_unsubmitted_task')
    async def lookup_operation(self, *args): raise unsupported('B lookup_operation')
    async def invalidate_operations(self, *args): raise unsupported('B invalidate_operations')
    async def check_mandate_scope(self, *args): raise unsupported('B mandate scope confirmation')


class UnavailableAnalytics:
    provenance = 'D'
    available = False

    async def execute_analysis_plan(self, *args): raise unsupported('D execute_analysis_plan')
    async def simulate_cashflow(self, *args): raise unsupported('D simulate_cashflow')
    async def compare_cashflow_scenarios(self, *args): raise unsupported('D compare_cashflow_scenarios')


class ExistingAnalysisAdapter(UnavailableAnalytics):
    """Only bridge the configured C1 analysis implementation; never add SQL in C3."""
    provenance = 'C1_fixture'

    def __init__(self, analysis):
        self.analysis = analysis

    def supports(self, tool_name):
        return tool_name=='analyze_transactions' and not isinstance(self.analysis,UnavailableAnalysis)

    async def execute_analysis_plan(self, principal, inputs):
        return self.analysis.analyze(inputs.plan, principal).model_dump()
