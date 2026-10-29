"""Additive C1 wire contracts. No field can grant execution authority."""

import re
from datetime import date
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, AfterValidator, field_validator, model_validator

class ClosedModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def valid_date(value: str) -> str:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('ISO date required')
    date.fromisoformat(value)
    return value


ISODate = Annotated[str, AfterValidator(valid_date)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Money = Annotated[int, Field(gt=0, le=2**63 - 1)]
NonNegativeMoney = Annotated[int, Field(ge=0, le=2**63 - 1)]
Category = Literal['餐饮', '交通', '购物', '娱乐', '订阅', '房租', '医疗', '其他', '工资', '转账']
IntentName = Literal['analysis', 'transfer', 'mandate', 'goal']
Action = Literal['analyze', 'propose_transfer', 'request_mandate_scope', 'propose_mandate_cancel', 'simulate_goal']
MissingField = Literal['analysis_plan', 'amount_cents', 'recipient_id', 'source_account_id',
                       'execution_date', 'mandate_id', 'mandate_action', 'goal_name', 'target_date']


class AnalysisFilters(ClosedModel):
    category: Category


class AnalysisPlan(ClosedModel):
    """Existing static analysis-plan v1 subset, not the broader Demo IntentPlan."""
    kind: Literal['analysis']
    dataset_id: Literal['personal_ledger']
    semantic_version: Literal[1]
    metric: Literal['gross_expense', 'refund', 'net_expense']
    start: ISODate
    end_exclusive: ISODate
    filters: AnalysisFilters
    group_by: None
    limit: int = Field(ge=1, le=100)

    @field_validator('semantic_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('integer version required')
        return value

    @model_validator(mode='after')
    def ordered_dates(self):
        days = (date.fromisoformat(self.end_exclusive) - date.fromisoformat(self.start)).days
        if not 0 < days <= 1096:
            raise ValueError('date range must be positive and at most three years')
        return self


class Entities(ClosedModel):
    analysis_plan: AnalysisPlan | None = None
    amount_cents: Money | None = None
    recipient_id: Identifier | None = None
    recipient_name: Identifier | None = None
    source_account_id: Identifier | None = None
    execution_date: ISODate | None = None
    mandate_id: Identifier | None = None
    mandate_name: Identifier | None = None
    mandate_action: Literal['list', 'cancel'] | None = None
    goal_name: Identifier | None = None
    target_date: ISODate | None = None


class Constraints(ClosedModel):
    read_only: bool = False
    protected_mandate_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    min_balance_cents: NonNegativeMoney | None = None


class ProposedStep(ClosedModel):
    action: Action


ALLOWED_ENTITIES = {
    'analysis': {'analysis_plan'},
    'transfer': {'amount_cents', 'recipient_id', 'recipient_name', 'source_account_id', 'execution_date'},
    'mandate': {'mandate_id', 'mandate_name', 'mandate_action', 'source_account_id'},
    'goal': {'goal_name', 'amount_cents', 'source_account_id', 'target_date'},
}
ALLOWED_STEPS = {'analysis': {'analyze'}, 'transfer': {'propose_transfer'},
                 'mandate': {'request_mandate_scope', 'propose_mandate_cancel'}, 'goal': {'simulate_goal'}}


class AgentIntent(ClosedModel):
    intent: IntentName
    entities: Entities
    constraints: Constraints
    missing_fields: list[MissingField] = Field(max_length=10)
    context_refs: list[Identifier] = Field(max_length=20)
    proposed_steps: list[ProposedStep] = Field(max_length=10)

    @model_validator(mode='after')
    def matching_intent(self):
        fields = set(self.entities.model_dump(exclude_none=True))
        if fields - ALLOWED_ENTITIES[self.intent]:
            raise ValueError('entity does not belong to intent')
        if any(s.action not in ALLOWED_STEPS[self.intent] for s in self.proposed_steps):
            raise ValueError('step does not belong to intent')
        return self


class SessionRequest(ClosedModel):
    dataset_id: Identifier = 'personal_ledger'


class SessionResult(ClosedModel):
    session_id: str
    dataset_id: str


class ChatRequest(ClosedModel):
    session_id: Identifier
    text: str = Field(min_length=1, max_length=2000)
    context_refs: list[Identifier] = Field(default_factory=list, max_length=20)

    @field_validator('text')
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError('text required')
        return value


class RecipientCandidate(ClosedModel):
    id: str
    name: str
    alias: str
    tail4: str


class ChatResult(ClosedModel):
    task_id: str
    plan_id: str
    plan_version: int
    context_ref: str
    intent: IntentName
    status: Literal['NEEDS_INFO', 'READ_ONLY', 'PROPOSED', 'BLOCKED', 'AWAITING_SCOPE_CONFIRMATION', 'CANCELLED']
    entities: Entities
    constraints: Constraints
    missing_fields: list[MissingField]
    questions: list[str]
    recipient_candidates: list[RecipientCandidate]
    proposed_steps: list[ProposedStep]
    execution_available: Literal[False] = False


class AnalysisRequest(ClosedModel):
    session_id: Identifier
    plan: AnalysisPlan


class AnalysisRow(ClosedModel):
    value_cents: int


class AnalysisResult(ClosedModel):
    rows: list[AnalysisRow]
    evidence_ids: list[str]
    chart: Literal['metric_card'] = 'metric_card'
    source: Literal['offline_fixture'] = 'offline_fixture'


class Assumptions(ClosedModel):
    salary_date: ISODate | None = None
    salary_cents: NonNegativeMoney | None = None
    daily_budget_cents: NonNegativeMoney | None = None


class SimulateRequest(ClosedModel):
    plan_version: int = Field(ge=1)
    assumptions: Assumptions = Field(default_factory=Assumptions)


class OperationRequest(ClosedModel):
    plan_id: Identifier
    plan_version: int = Field(ge=1)
    selected_action: Literal['propose_transfer', 'propose_mandate_cancel']


class ConfirmRequest(ClosedModel):
    plan_version: int = Field(ge=1)
    params_hash: Identifier


class VerifyRequest(ClosedModel):
    credential: SecretStr = Field(min_length=1, max_length=4096)


class ExecuteRequest(ClosedModel):
    plan_version: int = Field(ge=1)


class CancelRequest(ClosedModel):
    version: int = Field(ge=1)


class TaskResult(ClosedModel):
    task_id: str
    status: Literal['CANCELLED']
    version: int


class ErrorDetail(ClosedModel):
    code: str
    recoverable: bool
    next_action: str


T = TypeVar('T')


class Envelope(ClosedModel, Generic[T]):
    data: T | None = None
    error: ErrorDetail | None = None
    request_id: str
