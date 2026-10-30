"""Closed drafts are untrusted; runtime fields are constructed only by C."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..schemas import (AnalysisPlan, AnalysisResult, ClosedModel, Constraints, Identifier, ISODate,
                       Money, NonNegativeMoney)
from ..ports import DailyPoint
from .graph import ancestors, topological_order


class BalanceInputs(ClosedModel):
    source_account_id: Identifier


class RecipientInputs(BalanceInputs):
    recipient_id: Identifier


class TransferInputs(RecipientInputs):
    amount_cents: Money
    execution_date: ISODate


class AnalysisInputs(ClosedModel):
    plan: AnalysisPlan


class CashflowInputs(BalanceInputs):
    amount_cents: Money
    target_date: ISODate
    salary_date: ISODate | None = None


class MandateInputs(BalanceInputs):
    mandate_id: Identifier | None = None


Inputs = TransferInputs | RecipientInputs | CashflowInputs | MandateInputs | BalanceInputs | AnalysisInputs
ToolName = Literal['bank.balance', 'bank.recipient', 'bank.quote', 'analytics.analysis',
                   'cashflow.simulate', 'execution.transfer', 'execution.cancel_mandate', 'execution.mandate_scope']
TOOL_INPUTS = {'bank.balance': BalanceInputs, 'bank.recipient': RecipientInputs,
               'bank.quote': TransferInputs, 'analytics.analysis': AnalysisInputs,
               'cashflow.simulate': CashflowInputs, 'execution.transfer': TransferInputs,
               'execution.cancel_mandate': MandateInputs, 'execution.mandate_scope': MandateInputs}
WRITE_TOOLS = {'execution.transfer', 'execution.cancel_mandate'}
RESULT_FIELDS = {'bank.balance': {'available_cents': int}, 'bank.recipient': {'verified': bool},
                 'bank.quote': {'valid': bool, 'fee_cents': int}, 'cashflow.simulate': {'min_balance_cents': int}}


class Expectation(ClosedModel):
    field: Literal['available_cents', 'verified', 'valid', 'fee_cents', 'min_balance_cents']
    value: int | bool


class Precondition(Expectation):
    node_id: Identifier
    op: Literal['eq', 'gte'] = 'eq'


class NodeDraft(ClosedModel):
    node_id: Identifier
    tool_name: ToolName
    inputs: Inputs
    depends_on: list[Identifier] = Field(default_factory=list, max_length=64)
    preconditions: list[Precondition] = Field(default_factory=list, max_length=20)
    expected_result: list[Expectation] = Field(default_factory=list, max_length=10)

    @model_validator(mode='before')
    @classmethod
    def select_tool_inputs(cls, value):
        if isinstance(value, dict) and value.get('tool_name') in TOOL_INPUTS:
            value = dict(value)
            value['inputs'] = TOOL_INPUTS[value['tool_name']].model_validate(value.get('inputs'))
        return value

    @model_validator(mode='after')
    def matching_input(self):
        # Exact class prevents a wider union member from extending a narrower tool.
        if type(self.inputs) is not TOOL_INPUTS[self.tool_name]:
            raise ValueError('tool input schema mismatch')
        if self.tool_name == 'execution.cancel_mandate' and not self.inputs.mandate_id:
            raise ValueError('mandate_id required')
        for expected in self.expected_result:
            if type(expected.value) is not RESULT_FIELDS.get(self.tool_name, {}).get(expected.field):
                raise ValueError('invalid expected result field/type')
        return self


class GraphDefinition(ClosedModel):
    nodes: list[NodeDraft] = Field(min_length=1, max_length=64)

    @model_validator(mode='after')
    def valid_graph(self):
        topological_order(self.nodes)
        by_id = {n.node_id: n for n in self.nodes}
        for node in self.nodes:
            reachable = ancestors(self.nodes, node.node_id)
            for condition in node.preconditions:
                if condition.node_id not in reachable:
                    raise ValueError('precondition must reference an ancestor')
                tool = by_id[condition.node_id].tool_name
                if type(condition.value) is not RESULT_FIELDS.get(tool, {}).get(condition.field):
                    raise ValueError('precondition field/type mismatch')
                if condition.op == 'gte' and type(condition.value) is not int:
                    raise ValueError('gte requires integer')
        return self


class DagCreate(GraphDefinition):
    session_id: Identifier


class NodeState(NodeDraft):
    risk_level: Literal['READ_ONLY', 'B_REQUIRED']
    status: Literal['PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'WAITING_B', 'PENDING_UNKNOWN'] = 'PENDING'
    result_ref: str | None = None
    compensation: Literal['NONE', 'MANUAL_REVIEW_ONLY'] = 'NONE'
    error_code: str | None = None
    operation_id: str | None = None


class InputChange(ClosedModel):
    node_id: str
    before: Inputs
    after: Inputs
    applied: bool


class ResultChange(ClosedModel):
    node_id: str
    field: str
    expected: int | bool
    actual: int | bool
    result_ref: str


class VersionDiff(ClosedModel):
    reason: str
    previous_version: int | None = None
    changes: list[InputChange] = Field(default_factory=list)
    affected_nodes: list[str] = Field(default_factory=list)
    retained_nodes: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    result_changes: list[ResultChange] = Field(default_factory=list)


class DagPlan(ClosedModel):
    plan_id: str
    session_id: str
    plan_version: int
    state: Literal['ACTIVE', 'SUPERSEDED', 'REQUIRES_REPLAN'] = 'ACTIVE'
    nodes: list[NodeState]
    constraints: Constraints
    diff: VersionDiff
    origin_c1_plan_id: str | None = None


class PlanUpdates(ClosedModel):
    amount_cents: Money | None = None
    recipient_id: Identifier | None = None
    execution_date: ISODate | None = None
    salary_date: ISODate | None = None

    @field_validator('*', mode='before')
    @classmethod
    def no_explicit_null(cls, value):
        if value is None:
            raise ValueError('omit unchanged fields instead of null')
        return value


class ReplanRequest(ClosedModel):
    plan_version: int = Field(ge=1)
    reason: Literal['user_changed', 'constraints_changed']
    updates: PlanUpdates = Field(default_factory=PlanUpdates)

    @model_validator(mode='after')
    def nonempty_user_updates(self):
        if self.reason == 'user_changed' and not self.updates.model_fields_set:
            raise ValueError('updates required')
        return self


class VersionRequest(ClosedModel):
    plan_version: int = Field(ge=1)


class PrepareRequest(VersionRequest):
    node_id: Identifier


class BalanceResult(ClosedModel):
    available_cents: NonNegativeMoney


class RecipientResult(ClosedModel):
    recipient_id: Identifier
    verified: bool


class QuoteResult(ClosedModel):
    valid: bool
    fee_cents: NonNegativeMoney


class CashflowResult(ClosedModel):
    points: list[DailyPoint] = Field(max_length=1096)
    min_balance_cents: int
    conflicts: list[str] = Field(max_length=100)


class PreparedOperation(ClosedModel):
    operation_id: Identifier
    plan_id: Identifier
    plan_version: int = Field(ge=1)
    node_id: Identifier


class BOutcome(PreparedOperation):
    status: Literal['NEEDS_CONFIRM', 'NEEDS_AUTH', 'READY', 'SCHEDULED', 'SUBMITTING',
                    'PENDING_UNKNOWN', 'SUCCEEDED', 'FAILED']
    parameters: TransferInputs | MandateInputs
    bank_ref: Identifier | None = None

    @model_validator(mode='after')
    def success_has_evidence(self):
        if self.status == 'SUCCEEDED' and not self.bank_ref:
            raise ValueError('bank reference required')
        return self


class InvalidationReceipt(ClosedModel):
    plan_id: Identifier
    plan_version: int = Field(ge=1)
    operation_ids: list[Identifier]
    invalidated: Literal[True]

    @field_validator('invalidated', mode='before')
    @classmethod
    def strict_true(cls, value):
        if value is not True:
            raise ValueError('explicit boolean true required')
        return value


class ActionProposal(ClosedModel):
    plan_id: Identifier
    plan_version: int
    node_id: Identifier
    tool_name: Literal['execution.transfer', 'execution.cancel_mandate']
    inputs: TransferInputs | MandateInputs
    constraints: Constraints


class ResultRecord(ClosedModel):
    result_ref: str
    node_id: str
    tool_name: ToolName
    origin_plan_version: int
    source: Literal['A', 'B', 'D', 'mock_A', 'mock_B', 'mock_D', 'C1_fixture']
    data: BalanceResult | RecipientResult | QuoteResult | CashflowResult | AnalysisResult | BOutcome
