"""Optional in-process request replay protection; not durable payment idempotency."""

import asyncio
import hashlib

from ..errors import AgentError
from .events import request_id
from ..sensitive import reject_credentials


class RequestReplay:
    def __init__(self,capacity=256):
        self.capacity=capacity
        self.entries={}

    async def call(self,key,body,principal,store,workflow):
        # Re-check scope even on cache hits. Never cache authorization credentials.
        store.session(body.session_id,principal)
        reject_credentials(body.text)
        if not key.isascii() or not 1<=len(key)<=128:
            raise AgentError('INVALID_REQUEST',422)
        slot=(principal.subject,hashlib.sha256(key.encode()).hexdigest())
        fingerprint=hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        existing=self.entries.get(slot)
        if existing:
            previous,task=existing
            if previous!=fingerprint:
                raise AgentError('IDEMPOTENCY_CONFLICT',409)
        else:
            if len(self.entries)>=self.capacity:
                raise AgentError('CAPACITY_EXCEEDED',503)
            task=asyncio.create_task(workflow())
            # Consume failures without logging payloads if the client disconnects.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            self.entries[slot]=(fingerprint,task)
        if task.cancelled():
            raise AgentError('PENDING_UNKNOWN',409,next_action='本实例请求已中断，先查证已有任务。')
        report=await asyncio.shield(task)
        store.session(body.session_id,principal)
        report=report.model_copy(deep=True)
        report.request_id=request_id.get()
        return report
