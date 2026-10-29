"""Principal-bound, single-process C1 planning state; never bank authority."""

import asyncio
import copy
import secrets
from dataclasses import dataclass, field

from .auth import Principal
from .errors import AgentError, forbidden
from .schemas import ChatResult, Constraints


def new_id(prefix: str):
    return prefix + '_' + secrets.token_urlsafe(18)


@dataclass
class Session:
    id: str
    subject: str
    dataset_id: str
    account_ids: tuple[str, ...]
    constraints: Constraints
    records: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class Record:
    session_id: str
    subject: str
    result: ChatResult


class ContextStore:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.records: dict[str, Record] = {}
        self.plans: dict[str, str] = {}
        self.tasks: dict[str, str] = {}
        self.events: list[dict] = []

    def create_session(self, principal: Principal, dataset_id: str, protected: set[str]):
        if dataset_id != 'personal_ledger' or dataset_id not in principal.dataset_ids:
            raise forbidden()
        if len(self.sessions) >= 1000:
            raise AgentError('CAPACITY_EXCEEDED', 503, next_action='本实例会话容量已满，请稍后重试。')
        session = Session(new_id('session'), principal.subject, dataset_id, principal.account_ids,
                          Constraints(protected_mandate_ids=sorted(protected)))
        self.sessions[session.id] = session
        return session

    def session(self, id: str, principal: Principal) -> Session:
        session = self.sessions.get(id)
        if (session is None or session.subject != principal.subject or
                session.dataset_id not in principal.dataset_ids or
                not set(session.account_ids).issubset(principal.account_ids)):
            raise forbidden()
        return session

    def scope(self, session: Session, principal: Principal) -> Principal:
        self.session(session.id, principal)
        # New grants never silently broaden an existing session.
        return Principal(principal.subject, session.account_ids, (session.dataset_id,))

    def reference(self, id: str, session: Session) -> Record:
        record = self.records.get(id)
        if record is None or record.subject != session.subject or record.session_id != session.id:
            raise forbidden()
        if record.result.status == 'CANCELLED':
            raise AgentError('PLAN_STALE', 409, next_action='该提议已取消，请创建新提议。')
        return record

    def plan(self, id: str, principal: Principal, version: int | None = None) -> Record:
        record = self.records.get(self.plans.get(id, ''))
        if record is None or record.subject != principal.subject:
            raise forbidden()
        session = self.session(record.session_id, principal)
        if record.result.status == 'CANCELLED' or (version is not None and version != record.result.plan_version):
            raise AgentError('PLAN_STALE', 409)
        if session.constraints != record.result.constraints:
            raise AgentError('PLAN_STALE', 409, next_action='会话业务约束已更新，请生成新提议。')
        return record

    def save(self, session: Session, result: ChatResult):
        if len(session.records) >= 100:
            raise AgentError('CAPACITY_EXCEEDED', 409, next_action='请创建新会话；原有约束不会作为交易授权迁移。')
        record = Record(session.id, session.subject, result.model_copy(deep=True))
        self.records[result.context_ref] = record
        self.plans[result.plan_id] = result.context_ref
        self.tasks[result.task_id] = result.context_ref
        session.records.append(result.context_ref)
        session.constraints = result.constraints.model_copy(deep=True)
        self.emit(record)

    def emit(self, record: Record):
        result = record.result
        self.events.append(dict(id=len(self.events) + 1, subject=record.subject,
                                data=dict(task_id=result.task_id, plan_id=result.plan_id,
                                          plan_version=result.plan_version, status=result.status)))

    def cancel(self, task_id: str, principal: Principal, version: int):
        record = self.records.get(self.tasks.get(task_id, ''))
        if record is None or record.subject != principal.subject:
            raise forbidden()
        self.session(record.session_id, principal)
        if version != record.result.plan_version:
            raise AgentError('PLAN_STALE', 409)
        if record.result.status != 'CANCELLED':
            record.result.status = 'CANCELLED'
            self.emit(record)
        return dict(task_id=task_id, status='CANCELLED', version=version)

    def replay(self, principal: Principal, after: int):
        return [copy.deepcopy(event) for event in self.events
                if event['subject'] == principal.subject and event['id'] > after]
