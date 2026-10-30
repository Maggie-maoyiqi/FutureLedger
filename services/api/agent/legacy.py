"""C-owned binding guards for explicit legacy B commands, never B authorization."""

import asyncio
from dataclasses import dataclass, field

from pydantic import ValidationError

from .errors import AgentError, forbidden, unsupported
from .ports import OperationState
from .sensitive import contains_secret


@dataclass
class Binding:
    subject: str
    plan_id: str
    version: int
    view: OperationState
    unknown: bool = False
    execute_attempted: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LegacyBoundary:
    def __init__(self, store, gateway):
        self.store,self.gateway=store,gateway
        self.bindings={}

    def _parse(self,raw,*,id=None,plan_id,version):
        try:
            # Pydantic otherwise trusts already-constructed (possibly mutated) instances.
            payload=raw.model_dump(warnings=False) if isinstance(raw,OperationState) else raw
            view=OperationState.model_validate(payload).model_copy(deep=True)
            if (id is not None and view.operation_id!=id) or view.plan_id!=plan_id or view.plan_version!=version:
                raise ValueError('binding mismatch')
            if contains_secret(view.model_dump_json()):
                raise ValueError('sensitive response')
        except (ValidationError,ValueError,TypeError):
            raise AgentError('INVALID_UPSTREAM_RESULT',502) from None
        # Free-form upstream guidance is not an authority channel or credential echo.
        actions={'NEEDS_CONFIRM':'请在 B 明确确认参数。','NEEDS_AUTH':'请在独立 B 表单完成验证。',
                 'READY':'由 B 决定是否可以提交。','SUCCEEDED':'查看 B 的权威结果。',
                 'FAILED':'查看 B 的明确失败原因；新动作需重新确认。'}
        view.next_action=actions.get(view.status,'查询 B 状态；不盲目重试付款。')
        return view

    def register(self,p,snapshot,raw):
        view=self._parse(raw,plan_id=snapshot.plan_id,version=snapshot.plan_version)
        old=self.bindings.get(view.operation_id)
        if old and (old.subject!=p.subject or old.plan_id!=snapshot.plan_id or old.version!=snapshot.plan_version):
            raise forbidden()
        if old is None and view.status not in {'DRAFT','NEEDS_INFO','NEEDS_CONFIRM'}:
            raise AgentError('INVALID_UPSTREAM_RESULT',502)
        if old is not None:
            return old.view.model_copy(deep=True)
        self.bindings[view.operation_id]=Binding(p.subject,snapshot.plan_id,snapshot.plan_version,view)
        return view.model_copy(deep=True)

    def _access(self,p,id,version=None,*,read=False):
        bound=self.bindings.get(id)
        if bound is None:
            raise unsupported('未在本实例绑定的 B 操作')
        if bound.subject!=p.subject:
            raise forbidden()
        if version is not None and version!=bound.version:
            raise AgentError('PLAN_STALE',409)
        record=self.store.records[self.store.plans[bound.plan_id]]
        self.store.session(record.session_id,p)
        if not read:
            self.store.plan(bound.plan_id,p,bound.version)
            if bound.unknown:
                raise AgentError('PENDING_UNKNOWN',409,next_action='先查询 B 状态，不重新执行付款。')
        return bound

    async def command(self,p,id,kind,command=None,*,key=None):
        bound=self._access(p,id,getattr(command,'plan_version',None),read=kind=='get')
        async with bound.lock:
            return await self._command(p,id,kind,command,key=key)

    async def _command(self,p,id,kind,command=None,*,key=None):
        version=getattr(command,'plan_version',None)
        bound=self._access(p,id,version,read=kind=='get')
        # A stale B observation must never erase a recorded successful side effect.
        if bound.view.status=='SUCCEEDED':
            return bound.view.model_copy(deep=True)
        if kind=='execute':
            if bound.view.status=='SUCCEEDED':
                return bound.view.model_copy(deep=True)
            if bound.execute_attempted:
                raise AgentError('PENDING_UNKNOWN',409,next_action='该操作已提交过；只查询 B 或由 B 恢复，不再次付款。')
            if bound.view.status!='READY':
                raise AgentError('AUTH_REQUIRED',409,next_action='C 不批准操作；先完成 B 确认与必要验证。')
        try:
            if kind=='execute':
                bound.execute_attempted=True
                raw=await asyncio.wait_for(self.gateway.execute(p,id,command,key),25)
            elif kind=='get':
                raw=await asyncio.wait_for(self.gateway.get(p,id),25)
            else:
                raw=await asyncio.wait_for(getattr(self.gateway,kind)(p,id,command),25)
            view=self._parse(raw,id=id,plan_id=bound.plan_id,version=bound.version)
        except asyncio.CancelledError:
            if kind=='execute': bound.unknown=True
            raise
        except Exception as exc:
            if kind=='execute':
                bound.unknown=True
                raise AgentError('PENDING_UNKNOWN',409,next_action='执行回包未核实，仅查询 B 状态。') from None
            if isinstance(exc,AgentError):
                code=exc.code if exc.code in {'AUTH_REQUIRED','AUTH_EXPIRED','FORBIDDEN','PLAN_STALE',
                    'UNSUPPORTED','INVALID_UPSTREAM_RESULT','UPSTREAM_UNAVAILABLE'} else 'UPSTREAM_UNAVAILABLE'
                raise AgentError(code,exc.status,next_action='通过 B 的独立流程处理当前状态。') from None
            raise AgentError('UPSTREAM_UNAVAILABLE',503) from None
        bound.view=view
        bound.unknown=view.status in {'SUBMITTING','PENDING_UNKNOWN'}
        return view.model_copy(deep=True)
