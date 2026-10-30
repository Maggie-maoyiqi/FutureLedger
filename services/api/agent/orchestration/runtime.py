"""Registry bridges reuse C2's scoped runner, result refs and version checks."""

from ..schemas import Constraints
from ..dag.schemas import InvalidationReceipt
from .registry import build_registry
from .schemas import CallContext, CashflowReport, CancellationReceipt


class ToolRuntime:
    def __init__(self, directory, bank, gate, analytics, *, timeout=5):
        for role, adapter in [('A', bank), ('B', gate), ('D', analytics)]:
            allowed = {role, 'mock_'+role} | ({'C1_fixture'} if role == 'D' else set())
            if adapter.provenance not in allowed:
                raise ValueError('adapter provenance must identify its module/mock source')
        self.bank, self.b, self.d = bank, gate, analytics
        self.registry = build_registry(directory,bank,gate,analytics,timeout=timeout)
        self.details = {}
        self.reads = RegistryReads(self)
        self.gate = RegistryGate(self)


class RegistryReads:
    def __init__(self, runtime):
        self.runtime = runtime

    def source(self, tool_name):
        return self.runtime.bank.provenance if tool_name.startswith('bank.') else self.runtime.d.provenance

    async def read_node(self, plan, node, principal, dependencies):
        ctx = CallContext(principal,plan.constraints.model_copy(deep=True),plan.plan_id,plan.plan_version,node.node_id)
        output = await self.runtime.registry.invoke(node.tool_name,node.inputs,ctx)
        self.runtime.details[(plan.plan_id,plan.plan_version,node.node_id)] = output.model_copy(deep=True)
        if isinstance(output,CashflowReport):
            return output.model_dump(include={'points','min_balance_cents','conflicts'})
        return output.model_dump()


class RegistryGate:
    def __init__(self, runtime):
        self.runtime = runtime
        self.source = runtime.b.provenance

    async def prepare(self, principal, proposal):
        ctx=CallContext(principal,proposal.constraints.model_copy(deep=True),proposal.plan_id,proposal.plan_version,proposal.node_id)
        tool='propose_transfer' if proposal.tool_name=='execution.transfer' else 'propose_mandate_cancellation'
        return (await self.runtime.registry.invoke(tool,proposal,ctx)).model_dump()

    async def observe(self, principal, binding):
        ctx=CallContext(principal,Constraints(),binding.plan_id,binding.plan_version,binding.node_id)
        return (await self.runtime.registry.invoke('get_operation_status',binding,ctx)).model_dump()

    async def lookup(self, principal, proposal):
        return await self.runtime.b.lookup_operation(principal,proposal)

    async def invalidate(self, principal, plan_id, version, operation_ids):
        return await self.runtime.b.invalidate_operations(principal,plan_id,version,operation_ids)

    async def cancel(self, principal, binding):
        raw = await self.runtime.b.cancel_unsubmitted_task(principal,binding.model_copy(deep=True))
        result = CancellationReceipt.model_validate(raw)
        for key in ('operation_id','plan_id','plan_version','node_id'):
            if getattr(binding,key)!=getattr(result,key):
                raise ValueError('cancellation binding mismatch')
        return result
