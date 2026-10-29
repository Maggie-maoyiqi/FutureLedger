"""Typed C -> B/D handoffs. These are proposals/estimates, never authorization.

An implementation must enforce principal ownership for *every* operation ID.
B owns canonical hashing, expiry, verification, quota, idempotency and bank truth.
D owns numerical calculation. Neither receives a model-created permission.
"""

from typing import Literal, Protocol

from .auth import Principal
from .errors import unsupported
from .schemas import (Assumptions, ClosedModel, ConfirmRequest, Constraints, Entities,
                      ExecuteRequest, ISODate, VerifyRequest)


class ProposalSnapshot(ClosedModel):
    plan_id: str
    plan_version: int
    intent: Literal['analysis', 'transfer', 'mandate', 'goal']
    entities: Entities
    constraints: Constraints


class OperationState(ClosedModel):
    operation_id: str
    plan_id: str
    plan_version: int
    status: Literal['DRAFT', 'NEEDS_INFO', 'NEEDS_CONFIRM', 'NEEDS_AUTH', 'READY', 'SCHEDULED',
                    'SUBMITTING', 'SUCCEEDED', 'FAILED', 'PENDING_UNKNOWN']
    bank_ref: str | None = None
    next_action: str


class DailyPoint(ClosedModel):
    day: ISODate
    balance_cents: int


class SimulationResult(ClosedModel):
    plan_id: str
    plan_version: int
    points: list[DailyPoint]
    conflicts: list[str]
    assumptions: Assumptions
    estimate_only: Literal[True] = True


class ExecutionGateway(Protocol):
    async def propose(self, principal: Principal, snapshot: ProposalSnapshot, action: str) -> OperationState: ...
    async def confirm(self, principal: Principal, id: str, command: ConfirmRequest) -> OperationState: ...
    async def verify(self, principal: Principal, id: str, command: VerifyRequest) -> OperationState: ...
    async def execute(self, principal: Principal, id: str, command: ExecuteRequest,
                      idempotency_key: str) -> OperationState: ...
    async def get(self, principal: Principal, id: str) -> OperationState: ...


class CashflowGateway(Protocol):
    async def simulate(self, principal: Principal, snapshot: ProposalSnapshot,
                       assumptions: Assumptions) -> SimulationResult: ...


class UnavailableExecution:
    """Fail-closed mock boundary; never generates an operation or bank result."""
    async def propose(self, principal, snapshot, action):
        raise unsupported('B 执行门')

    async def confirm(self, principal, id, command):
        raise unsupported('B 确认服务')

    async def verify(self, principal, id, command):
        raise unsupported('B 独立验证服务')

    async def execute(self, principal, id, command, idempotency_key):
        raise unsupported('B 执行门')

    async def get(self, principal, id):
        raise unsupported('B 权威操作状态服务')


class UnavailableCashflow:
    async def simulate(self, principal, snapshot, assumptions):
        raise unsupported('D 现金流计算')
