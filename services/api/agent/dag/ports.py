"""A/D reads and B authority ports. C has no submit/execute/compensate method."""

from typing import Protocol

from ..auth import Principal
from ..errors import unsupported
from .schemas import ActionProposal, Inputs, PreparedOperation


class ReadTools(Protocol):
    def source(self, tool_name: str) -> str: ...
    async def read(self, tool_name: str, principal: Principal, inputs: Inputs, dependencies: dict) -> object: ...


class BankExecution(Protocol):
    source: str
    async def prepare(self, principal: Principal, proposal: ActionProposal) -> object: ...
    async def observe(self, principal: Principal, binding: PreparedOperation) -> object: ...
    async def lookup(self, principal: Principal, proposal: ActionProposal) -> object: ...
    async def invalidate(self, principal: Principal, plan_id: str, version: int, operation_ids: list[str]) -> object: ...


class UnavailableReads:
    def source(self, tool_name):
        return 'A' if tool_name.startswith('bank.') else 'D'

    async def read(self, tool_name, principal, inputs, dependencies):
        raise unsupported('A/D DAG 工具 ' + tool_name)


class AnalysisReads(UnavailableReads):
    """Bridge the already configured C1 analysis adapter, never its Demo write path."""
    def __init__(self, analysis):
        self.analysis = analysis

    def source(self, tool_name):
        return 'C1_fixture' if tool_name == 'analytics.analysis' else super().source(tool_name)

    async def read(self, tool_name, principal, inputs, dependencies):
        if tool_name == 'analytics.analysis':
            return self.analysis.analyze(inputs.plan, principal).model_dump()
        return await super().read(tool_name, principal, inputs, dependencies)


class UnavailableBankExecution:
    source = 'B'

    async def prepare(self, principal, proposal):
        raise unsupported('B DAG 提议/版本绑定')

    async def observe(self, principal, binding):
        raise unsupported('B 权威操作查证')

    async def lookup(self, principal, proposal):
        raise unsupported('B 按稳定计划/版本/节点查找提议')

    async def invalidate(self, principal, plan_id, version, operation_ids):
        raise unsupported('B 旧版本凭证失效')
