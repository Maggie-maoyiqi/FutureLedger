"""C3 contracts are additive; C1/C2 wire models remain unchanged."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from ..auth import Principal
from ..schemas import ClosedModel, Constraints, ISODate, Identifier, ChatResult, AnalysisResult
from ..dag.schemas import (BalanceInputs, CashflowInputs, PreparedOperation, BalanceResult,
                            RecipientResult, QuoteResult, BOutcome)
from ..ports import DailyPoint


@dataclass(frozen=True)
class CallContext:
    principal: Principal
    constraints: Constraints
    plan_id: str
    plan_version: int
    node_id: str
    scope_ref: str | None = None


class ExecutionLimits(ClosedModel):
    max_nodes: int = Field(default=32, ge=1, le=64)
    max_depth: int = Field(default=12, ge=1, le=64)
    concurrency: Literal[1] = 1
    max_seconds: float = Field(default=30, gt=0, le=120)
    node_timeout_seconds: float = Field(default=5, gt=0, le=30)


class TransactionsInputs(BalanceInputs):
    start: ISODate
    end_exclusive: ISODate
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode='after')
    def ordered(self):
        if self.start >= self.end_exclusive:
            raise ValueError('invalid date range')
        return self


class TransactionView(ClosedModel):
    id: Identifier
    account_id: Identifier
    posted_on: ISODate
    amount_cents: int
    merchant: str = Field(max_length=200)
    memo: str = Field(max_length=1000)


class TransactionsResult(ClosedModel):
    transactions: list[TransactionView] = Field(max_length=100)


class TransferStatusInputs(BalanceInputs):
    reference: Identifier


class MandateStatusInputs(BalanceInputs):
    mandate_id: Identifier


class FactStatus(ClosedModel):
    reference: Identifier
    status: Literal['ACTIVE', 'CANCELLED', 'SUCCEEDED', 'FAILED', 'PENDING_UNKNOWN']


class TransferFactStatus(FactStatus):
    status: Literal['SUCCEEDED','FAILED','PENDING_UNKNOWN']


class MandateFactStatus(FactStatus):
    status: Literal['ACTIVE','CANCELLED','PENDING_UNKNOWN']


class MandateView(ClosedModel):
    mandate_id: Identifier
    account_id: Identifier
    merchant: str = Field(max_length=200)
    status: Literal['ACTIVE', 'CANCELLED', 'UNKNOWN']


class MandatesResult(ClosedModel):
    mandates: list[MandateView] = Field(max_length=100)


class ScopeGrant(ClosedModel):
    approved: bool
    subject: Identifier
    source_account_id: Identifier
    scope_ref: Identifier


class CashflowAssumptions(ClosedModel):
    source_account_id: Identifier
    target_date: ISODate
    target_amount_cents: int = Field(gt=0, le=2**63-1)
    salary_date: ISODate | None = None


class CashflowReport(ClosedModel):
    points: list[DailyPoint] = Field(max_length=1096)
    min_balance_cents: int
    conflicts: list[str] = Field(max_length=100)
    assumptions: CashflowAssumptions
    constraints: Constraints
    feasible: bool

    @model_validator(mode='after')
    def conflict_explanation(self):
        if not self.feasible and not self.conflicts:
            raise ValueError('infeasible scenario needs conflict reasons')
        if self.feasible and (self.conflicts or (self.constraints.min_balance_cents is not None
                and self.min_balance_cents<self.constraints.min_balance_cents)):
            raise ValueError('feasibility contradicts declared constraints')
        return self


class CompareInputs(ClosedModel):
    scenarios: list[CashflowInputs] = Field(min_length=1, max_length=3)


class CompareReport(ClosedModel):
    scenarios: list[CashflowReport] = Field(min_length=1, max_length=3)


class CancellationReceipt(PreparedOperation):
    status: Literal['CANCELLED', 'ALREADY_SUBMITTED', 'PENDING_UNKNOWN']


class CancellationEvidence(ClosedModel):
    source: Literal['B','mock_B']
    receipt: CancellationReceipt


class EvidenceRef(ClosedModel):
    node_id: str
    result_ref: str
    source: str
    evidence_ids: list[str] = Field(default_factory=list)


Output = AnalysisResult | CashflowReport | BalanceResult | RecipientResult | QuoteResult | BOutcome


class ExecutionReport(ClosedModel):
    task_id: str
    plan_id: str
    plan_version: int
    node_statuses: dict[str, str]
    execution_status: Literal['NEEDS_INFO', 'BLOCKED', 'AWAITING_CONFIRMATION', 'NEEDS_AUTH', 'READY_FOR_EXECUTION', 'SUBMITTED',
        'PENDING_UNKNOWN', 'COMPLETED', 'MOCK_COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'CANCEL_REQUESTED', 'INTERRUPTED']
    summary: str
    evidence_refs: list[EvidenceRef]
    assumptions: list[CashflowAssumptions]
    next_action: str
    request_id: str
    outputs: dict[str, Output] = Field(default_factory=dict)
    clarification: ChatResult | None = None
    cancellations: list[CancellationEvidence] = Field(default_factory=list)
