"""Finite C1 -> C2 -> registry execution and deterministic result aggregation."""

import asyncio

from ..context import new_id
from ..errors import AgentError
from ..dag.graph import topological_order
from ..dag.schemas import WRITE_TOOLS, BOutcome
from .events import AgentEvent, MemoryEvents, request_id
from .schemas import ExecutionLimits, ExecutionReport, CashflowReport


SUMMARIES = {
    'NEEDS_INFO': ('需要补充或消除歧义。','回答追问后再规划。'),
    'BLOCKED': ('当前约束或缺失的模块接口阻止了执行。','检查保护约束或接入所需模块。'),
    'AWAITING_CONFIRMATION': ('操作提议等待 B 确认，尚未付款。','通过 B 的独立确认流程继续。'),
    'NEEDS_AUTH': ('B 要求独立强验证，尚未确认执行结果。','在独立验证表单中操作，不将凭证发送到聊天。'),
    'READY_FOR_EXECUTION': ('B 已完成执行前准备，尚未付款。','通过 B 的独立执行接口提交；C 不自动付款。'),
    'SUBMITTED': ('操作已提交或预约，尚未确认成功。','查询 B 的权威状态。'),
    'PENDING_UNKNOWN': ('银行结果尚未确认，不能重复付款。','只查询 B 状态或交由 B 恢复。'),
    'COMPLETED': ('已完成并取得可核验结果。','查看数据、证据及假设。'),
    'MOCK_COMPLETED': ('模拟操作结果已完成，不是真实银行执行成功。','仅用于测试与联调。'),
    'PARTIAL': ('部分节点已完成，其他步骤仍需处理。','保留成功事实并处理未完成步骤。'),
    'FAILED': ('工具调用失败，相关下游未继续。','检查错误和模块可用性，不盲目重试付款。'),
    'CANCELLED': ('已停止尚未提交的工作，未撤销任何已发生资金事实。','已完成或已提交的操作仍以 B 为准。'),
    'CANCEL_REQUESTED': ('本地后续工作已停止，B 操作尚未确认取消。','继续查询 B，不将其视为撤销或失败。'),
    'INTERRUPTED': ('编排已因预算或中断停止。','查看当前节点状态；未知操作只交 B 查证。')}


class AgentOrchestrator:
    def __init__(self, service, engine, runtime, clock, *, limits=None, publisher=None):
        self.service, self.engine, self.runtime, self.clock = service, engine, runtime, clock
        self.limits = limits or ExecutionLimits()
        self.publisher = publisher or MemoryEvents(engine.store)
        self.stopped = set()
        self.cancelled_operations = set()
        self.cancellation_receipts = {}
        self.interrupted = set()
        self.completion_events = set()
        self.engine.event_sink = self._engine_event
        self.engine.access_guard = self._guard
        self.engine.observation_guard = lambda binding: binding.operation_id not in self.cancelled_operations

    def _guard(self,id):
        if id in self.stopped:
            raise AgentError('TASK_CANCELLED',409)

    def _task_id(self,plan):
        if plan.origin_c1_plan_id:
            return self.engine.store.records[self.engine.store.plans[plan.origin_c1_plan_id]].result.task_id
        return 'task_'+plan.plan_id

    def _event(self,kind,plan,node=None,*,status=None):
        history=self.engine.histories[plan.plan_id]
        event=AgentEvent(event_type=kind,request_id=request_id.get(),task_id=self._task_id(plan),
            plan_id=plan.plan_id,plan_version=plan.plan_version,node_id=node.node_id if node else None,
            status=status or (node.status if node else plan.state))
        self.publisher.publish(history.subject,event)

    def _engine_event(self,kind,plan,node=None):
        self._event(kind,plan,node)

    def _completed(self,plan,report):
        key=(plan.plan_id,plan.plan_version,report.execution_status)
        if report.execution_status in {'COMPLETED','MOCK_COMPLETED','FAILED'} and key not in self.completion_events:
            self._event('task_completed',plan,status=report.execution_status)
            self.completion_events.add(key)

    def _preflight(self,plan):
        depth={}
        by_id={n.node_id:n for n in plan.nodes}
        for id in topological_order(plan.nodes):
            depth[id]=1+max((depth[d] for d in by_id[id].depends_on),default=0)
        if len(plan.nodes)>self.limits.max_nodes or max(depth.values())>self.limits.max_depth:
            raise AgentError('EXECUTION_BUDGET_EXCEEDED',409,next_action='请缩小计划规模或依赖深度。')

    async def chat(self,body,principal):
        created=[]
        async def workflow():
            intent=await self.service.chat(body,principal,self.clock())
            if intent.missing_fields or intent.status=='BLOCKED':
                status='BLOCKED' if intent.status=='BLOCKED' else 'NEEDS_INFO'
                summary,next_action=SUMMARIES[status]
                return ExecutionReport(task_id=intent.task_id,plan_id=intent.plan_id,plan_version=1,
                    node_statuses={},execution_status=status,summary=summary,evidence_refs=[],assumptions=[],
                    next_action=next_action,request_id=request_id.get(),clarification=intent)
            plan=self.engine.from_c1(intent.plan_id,1,principal)
            created.append(plan.plan_id)
            return await self._run(plan.plan_id,1,principal,autopropose=True)
        try:
            return await asyncio.wait_for(workflow(),self.limits.max_seconds)
        except asyncio.TimeoutError:
            if created:
                plan=self.engine.get(created[0],principal)
                self.interrupted.add((plan.plan_id,plan.plan_version))
                self._event('task_interrupted',plan)
                return self.report(plan.plan_id,principal)
            raise AgentError('EXECUTION_BUDGET_EXCEEDED',503,next_action='请求预算已耗尽，未确认的操作只交由 B 查证。') from None

    async def _run(self,id,version,principal,*,autopropose=False):
        plan=self.engine.get(id,principal)
        self._preflight(plan)
        self.interrupted.discard((id,version))
        plan=await self.engine.run(id,version,principal)
        if autopropose:
            for node in plan.nodes:
                if node.tool_name not in WRITE_TOOLS or node.status!='WAITING_B' or node.operation_id:
                    continue
                try:
                    await self.engine.prepare(id,plan.plan_version,node.node_id,principal)
                except AgentError as exc:
                    current=self.engine.histories[id].versions[-1]
                    target=next(n for n in current.nodes if n.node_id==node.node_id)
                    target.error_code=exc.code
            plan=await self.engine.run(id,plan.plan_version,principal)
        report=self.report(id,principal)
        self._completed(plan,report)
        return report

    async def run(self,id,version,principal):
        self._guard(id)
        self._preflight(self.engine.get(id,principal))
        try:
            return await asyncio.wait_for(self._run(id,version,principal),self.limits.max_seconds)
        except asyncio.TimeoutError:
            self.interrupted.add((id,version))
            self._event('task_interrupted',self.engine.get(id,principal))
            return self.report(id,principal)

    async def replan(self,id,body,principal):
        async def workflow():
            plan=await self.engine.replan(id,body,principal)
            return await self._run(id,plan.plan_version,principal)
        try:
            return await asyncio.wait_for(workflow(),self.limits.max_seconds)
        except asyncio.TimeoutError:
            plan=self.engine.get(id,principal)
            self.interrupted.add((id,plan.plan_version))
            self._event('task_interrupted',plan)
            return self.report(id,principal)

    async def reconcile(self,id,version,principal):
        try:
            plan=await asyncio.wait_for(self.engine.reconcile(id,version,principal),self.limits.max_seconds)
        except asyncio.TimeoutError:
            plan=self.engine.get(id,principal)
            self.interrupted.add((id,plan.plan_version))
            self._event('task_interrupted',plan)
        report=self.report(id,principal)
        self._completed(plan,report)
        return report

    async def cancel(self,id,version,principal):
        self.engine._access(id,principal,version,allow_constraints=True,allow_cancelled=True)
        self.stopped.add(id)
        try:
            return await asyncio.wait_for(self._cancel(id,version,principal),self.limits.max_seconds)
        except asyncio.TimeoutError:
            self.interrupted.add((id,version))
            return self.report(id,principal)

    async def _cancel(self,id,version,principal):
        history,_,_,_=self.engine._access(id,principal,version,allow_constraints=True,allow_cancelled=True)
        async with history.lock:
            _,plan,session,scope=self.engine._access(id,principal,version,allow_constraints=True,allow_cancelled=True)
            async with session.lock:
                self.stopped.add(id)
                for node in plan.nodes:
                    if node.operation_id and node.status!='SUCCEEDED' and node.operation_id not in self.cancelled_operations:
                        binding=self.engine.bindings[node.operation_id][1]
                        try:
                            receipt=await asyncio.wait_for(self.runtime.gate.cancel(scope,binding),self.limits.node_timeout_seconds)
                            if receipt.status=='CANCELLED':
                                self.cancelled_operations.add(node.operation_id)
                                self.cancellation_receipts[node.operation_id]=receipt.model_copy(deep=True)
                            else:
                                node.status='PENDING_UNKNOWN'
                        except Exception:
                            node.status='PENDING_UNKNOWN'  # No cancellation proof: query B only.
                self._event('task_cancelled',plan,status=self.report(id,principal).execution_status)
        return self.report(id,principal)

    def report(self,id,principal):
        plan=self.engine.get(id,principal)
        outputs,evidence,assumptions={},[],[]
        for node in plan.nodes:
            if not node.result_ref:
                continue
            record=self.engine.result(id,node.result_ref,principal)
            detail=self.runtime.details.get((id,record.origin_plan_version,node.node_id),record.data)
            if node.operation_id not in self.cancelled_operations and (node.status=='SUCCEEDED' or isinstance(record.data,BOutcome)):
                outputs[node.node_id]=detail.model_copy(deep=True)
            ids=getattr(record.data,'evidence_ids',[])
            evidence.append(dict(node_id=node.node_id,result_ref=node.result_ref,source=record.source,evidence_ids=ids))
            if isinstance(detail,CashflowReport):
                assumptions.append(detail.assumptions.model_copy(deep=True))
        writes=[n for n in plan.nodes if n.tool_name in WRITE_TOOLS]
        uncertain=any(n.status=='PENDING_UNKNOWN' and n.operation_id not in self.cancelled_operations for n in writes)
        bstates=[self.engine.results[n.result_ref].data.status for n in writes
            if n.result_ref and isinstance(self.engine.results[n.result_ref].data,BOutcome)]
        if uncertain:
            status='PENDING_UNKNOWN'
        elif all(n.status=='SUCCEEDED' for n in plan.nodes):
            status='MOCK_COMPLETED' if any(n in writes and self.engine.results[n.result_ref].source=='mock_B' for n in plan.nodes) else 'COMPLETED'
        elif id in self.stopped:
            pending=any(n.operation_id and n.status!='SUCCEEDED' and n.operation_id not in self.cancelled_operations for n in writes)
            status='CANCEL_REQUESTED' if pending else 'CANCELLED'
        elif (id,plan.plan_version) in self.interrupted:
            status='INTERRUPTED'
        elif 'NEEDS_AUTH' in bstates:
            status='NEEDS_AUTH'
        elif 'NEEDS_CONFIRM' in bstates:
            status='AWAITING_CONFIRMATION'
        elif 'READY' in bstates:
            status='READY_FOR_EXECUTION'
        elif any(s in {'SCHEDULED','SUBMITTING'} for s in bstates):
            status='SUBMITTED'
        elif any(n.status=='FAILED' for n in plan.nodes):
            meaningful=WRITE_TOOLS | {'analytics.analysis','cashflow.simulate'}
            status='PARTIAL' if any(n.status=='SUCCEEDED' and n.tool_name in meaningful for n in plan.nodes) else 'FAILED'
        elif any(n.error_code=='UNSUPPORTED' for n in plan.nodes):
            status='BLOCKED'
        elif any(n.status=='SUCCEEDED' for n in plan.nodes):
            status='PARTIAL'
        else:
            status='BLOCKED'
        summary,next_action=SUMMARIES[status]
        return ExecutionReport(task_id=self._task_id(plan),plan_id=id,plan_version=plan.plan_version,
            node_statuses={n.node_id:'CANCELLED' if n.operation_id in self.cancelled_operations else n.status for n in plan.nodes},execution_status=status,summary=summary,
            evidence_refs=evidence,assumptions=assumptions,next_action=next_action,request_id=request_id.get(),outputs=outputs,
            cancellations=[{'source':self.runtime.b.provenance,'receipt':r.model_copy(deep=True)}
                for r in self.cancellation_receipts.values() if r.plan_id==id])
