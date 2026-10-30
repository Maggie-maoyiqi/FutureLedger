"""Metadata-only events using the existing in-process SSE store."""

from contextvars import ContextVar
from typing import Literal, Protocol

from ..schemas import ClosedModel

request_id = ContextVar('agent_request_id', default='internal')


class AgentEvent(ClosedModel):
    event_type: Literal['plan_created', 'node_started', 'node_completed', 'node_failed',
        'confirmation_required', 'operation_status_changed', 'plan_replanned', 'task_completed',
        'task_cancelled', 'task_interrupted']
    request_id: str
    task_id: str
    plan_id: str
    plan_version: int
    node_id: str | None = None
    status: str | None = None


class EventPublisher(Protocol):
    def publish(self, subject: str, event: AgentEvent) -> None: ...


class MemoryEvents:
    def __init__(self, store):
        self.store = store

    def publish(self, subject, event):
        self.store.events.append(dict(id=len(self.store.events)+1, subject=subject, data=event.model_dump()))
