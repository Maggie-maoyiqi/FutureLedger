"""C1/C2 composition root. Use services.api.dev:create_dev_app for explicit offline auth."""

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException

from .agent.adapters import FixtureDirectory, UnavailableAnalysis
from .agent.ports import (ExecutionGateway, CashflowGateway, OperationState, ProposalSnapshot,
                          SimulationResult, UnavailableCashflow, UnavailableExecution)
from .agent.auth import DenyAuthentication, Principal
from .agent.context import ContextStore
from .agent.errors import AgentError, forbidden
from .agent.schemas import (AnalysisRequest, AnalysisResult, CancelRequest, ChatRequest, ChatResult,
    ConfirmRequest, Envelope, ExecuteRequest, OperationRequest, SessionRequest, SessionResult,
    SimulateRequest, TaskResult, VerifyRequest)
from .agent.service import AgentService


ROOT = Path(__file__).resolve().parents[2]
bearer = HTTPBearer(auto_error=False)


def create_app(*, authenticator=None, directory=None, analysis=None, planner=None,
               execution: ExecutionGateway | None = None, cashflow: CashflowGateway | None = None,
               clock=None, provider_timeout=25, enable_c2=True, dag_tools=None, dag_execution=None,
               enable_c3=True, bank_read_adapter=None, analytics_adapter=None, execution_gate_adapter=None,
               agent_provider=None, orchestration_limits=None, enable_c4=True):
    enable_c3 = enable_c3 and enable_c2
    enable_c4 = enable_c4 and enable_c3
    app = FastAPI(title=('FutureLedger C4 Application API' if enable_c4 else 'FutureLedger C3 Application API' if enable_c3 else
                        'FutureLedger C2 Application API' if enable_c2 else 'FutureLedger C1 Application API'),
                  version='0.4.0' if enable_c4 else '0.3.0' if enable_c3 else '0.2.0' if enable_c2 else '0.1.0',
                  description=('C4 verified C module; real service integration and durable recovery remain external.' if enable_c4 else
                               'C3 bounded tool orchestration. Financial authority remains with B.' if enable_c3 else
                               'C2 versioned DAG orchestration. Financial execution remains owned by B.' if enable_c2 else
                               'C1 planning only. B execution and D cashflow are explicitly unsupported.'))
    auth = authenticator or DenyAuthentication()
    directory = directory or FixtureDirectory(ROOT / 'data')
    analysis = analysis or UnavailableAnalysis()
    execution = execution or UnavailableExecution()
    cashflow = cashflow or UnavailableCashflow()
    clock = clock or (lambda: datetime.now(ZoneInfo('Asia/Shanghai')).date())
    store = ContextStore()
    from .agent.legacy import LegacyBoundary
    legacy = LegacyBoundary(store,execution)
    if agent_provider is not None:
        from .agent.planner import ModelPlanner
        from .agent.orchestration.provider import StructuredProvider
        planner = ModelPlanner(StructuredProvider(agent_provider, timeout=min(provider_timeout,20)))
    service = AgentService(directory, store, planner, provider_timeout=provider_timeout)
    dag = None
    orchestrator = None
    if enable_c2:
        from .agent.dag.engine import DagEngine
        from .agent.dag.ports import AnalysisReads
        runtime = None
        if enable_c3:
            from .agent.orchestration.adapters import UnavailableBankRead, UnavailableGate, ExistingAnalysisAdapter
            from .agent.orchestration.runtime import ToolRuntime
            from .agent.orchestration.service import AgentOrchestrator
            from .agent.orchestration.schemas import ExecutionLimits
            limits = orchestration_limits or ExecutionLimits()
            runtime = ToolRuntime(directory,bank_read_adapter or UnavailableBankRead(),
                execution_gate_adapter or UnavailableGate(),analytics_adapter or ExistingAnalysisAdapter(analysis),
                timeout=limits.node_timeout_seconds)
        dag = DagEngine(store, directory, tools=dag_tools or (runtime.reads if runtime else AnalysisReads(analysis)),
                        bank_execution=dag_execution or (runtime.gate if runtime else None), clock=clock, timeout=provider_timeout)
        if runtime:
            orchestrator = AgentOrchestrator(service,dag,runtime,clock,limits=limits)

    def envelope(request, data=None):
        return dict(data=data, error=None, request_id=request.state.request_id)

    def failure(request, error):
        return JSONResponse(status_code=error.status, content=dict(data=None,
            error=dict(code=error.code, recoverable=error.recoverable, next_action=error.next_action),
            request_id=getattr(request.state, 'request_id', uuid4().hex)))

    @app.middleware('http')
    async def request_identity(request, call_next):
        request.state.request_id = uuid4().hex
        from .agent.orchestration.events import request_id as correlation_id
        correlation_token = correlation_id.set(request.state.request_id)
        try:
            response = await call_next(request)
        except Exception:
            # Never return upstream payloads, credentials or request validation inputs.
            response = failure(request, AgentError('INTERNAL_ERROR', 500,
                next_action='服务内部错误，请凭 request_id 联系维护者。'))
        finally:
            correlation_id.reset(correlation_token)
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @app.exception_handler(AgentError)
    async def agent_error(request, exc):
        return failure(request, exc)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return failure(request, AgentError('INVALID_REQUEST', 422,
                                          next_action='请按 OpenAPI 提供完整、严格类型的字段。'))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = 'NOT_FOUND' if exc.status_code == 404 else 'METHOD_NOT_ALLOWED' if exc.status_code == 405 else 'INVALID_REQUEST'
        return failure(request, AgentError(code, exc.status_code))

    async def principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        value = await auth.authenticate(credentials.credentials) if credentials else None
        if value is None:
            raise AgentError('AUTH_REQUIRED', 401, next_action='请提供由认证适配器验证的 Bearer 凭证。')
        return value

    errors = {code: {'model': Envelope[dict]} for code in (401, 403, 409, 422, 500, 501, 502, 503)}

    @app.post('/api/sessions', response_model=Envelope[SessionResult], responses=errors)
    async def create_session(body: SessionRequest, request: Request, p: Principal = Depends(principal)):
        if body.dataset_id not in p.dataset_ids:
            raise forbidden()
        session = store.create_session(p, body.dataset_id, directory.protected(p))
        return envelope(request, dict(session_id=session.id, dataset_id=session.dataset_id))

    @app.post('/api/chat', response_model=Envelope[ChatResult], responses=errors)
    async def chat(body: ChatRequest, request: Request, p: Principal = Depends(principal)):
        result = await service.chat(body, p, clock())
        return envelope(request, result)

    @app.post('/api/analysis', response_model=Envelope[AnalysisResult], responses=errors)
    async def analyze(body: AnalysisRequest, request: Request, p: Principal = Depends(principal)):
        session = store.session(body.session_id, p)
        scope = store.scope(session, p)
        if body.plan.dataset_id != session.dataset_id:
            raise forbidden()
        return envelope(request, analysis.analyze(body.plan, scope))

    def snapshot(record):
        r = record.result
        return ProposalSnapshot(plan_id=r.plan_id, plan_version=r.plan_version, intent=r.intent,
                                entities=r.entities.model_copy(deep=True), constraints=r.constraints.model_copy(deep=True))

    @app.post('/api/plans/{id}/simulate', response_model=Envelope[SimulationResult], responses=errors)
    async def simulate(id: str, body: SimulateRequest, request: Request, p: Principal = Depends(principal)):
        record = store.plan(id, p, body.plan_version)
        if dag:
            dag.guard_c1_plan(id)
        if record.result.intent != 'goal':
            raise AgentError('INVALID_ACTION', 422)
        return envelope(request, await cashflow.simulate(p, snapshot(record), body.assumptions))

    @app.post('/api/operations', response_model=Envelope[OperationState], responses=errors)
    async def propose(body: OperationRequest, request: Request, p: Principal = Depends(principal)):
        record = service.operation_proposal(p, body.plan_id, body.plan_version, body.selected_action, clock())
        if dag:
            dag.begin_legacy_proposal(body.plan_id)
        try:
            result = await execution.propose(p, snapshot(record), body.selected_action)
        except BaseException as exc:
            if dag:
                dag.finish_legacy_proposal(body.plan_id, unresolved=not (isinstance(exc, AgentError) and exc.code == 'UNSUPPORTED'))
            raise
        if dag:
            dag.finish_legacy_proposal(body.plan_id, unresolved=True)
        return envelope(request, legacy.register(p,snapshot(record),result))

    @app.post('/api/operations/{id}/confirm', response_model=Envelope[OperationState], responses=errors)
    async def confirm(id: str, body: ConfirmRequest, request: Request, p: Principal = Depends(principal)):
        if dag:
            dag.guard_legacy_operation(p, id)
        return envelope(request, await legacy.command(p,id,'confirm',body))

    @app.post('/api/operations/{id}/verify', response_model=Envelope[OperationState], responses=errors)
    async def verify(id: str, body: VerifyRequest, request: Request, p: Principal = Depends(principal)):
        # Credential never reaches Agent/provider/context/event storage.
        if dag:
            dag.guard_legacy_operation(p, id)
        return envelope(request, await legacy.command(p,id,'verify',body))

    @app.post('/api/operations/{id}/execute', response_model=Envelope[OperationState], responses=errors)
    async def execute(id: str, body: ExecuteRequest, request: Request,
                      idempotency_key: str = Header(min_length=1, max_length=128, alias='Idempotency-Key'),
                      p: Principal = Depends(principal)):
        if dag:
            dag.guard_legacy_operation(p, id)
        return envelope(request, await legacy.command(p,id,'execute',body,key=idempotency_key))

    @app.get('/api/operations/{id}', response_model=Envelope[OperationState], responses=errors)
    async def get_operation(id: str, request: Request, p: Principal = Depends(principal)):
        if dag:
            dag.guard_legacy_operation(p, id)
        return envelope(request, await legacy.command(p,id,'get'))

    @app.post('/api/tasks/{id}/cancel', response_model=Envelope[TaskResult], responses=errors)
    async def cancel(id: str, body: CancelRequest, request: Request, p: Principal = Depends(principal)):
        return envelope(request, store.cancel(id, p, body.version))

    @app.get('/api/events', response_class=StreamingResponse, responses={**errors,
        200: {'description': 'Finite principal-scoped SSE replay, in-memory only.',
              'content': {'text/event-stream': {'schema': {'type': 'string'}}}}})
    async def events(request: Request, p: Principal = Depends(principal),
                     last_event_id: str | None = Header(default=None, alias='Last-Event-ID')):
        if last_event_id is not None and (not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id) > 20):
            raise AgentError('INVALID_REQUEST', 422)
        after = int(last_event_id or 0)
        if after>len(store.events):
            raise AgentError('EVENT_CURSOR_UNAVAILABLE',409,next_action='本实例没有该游标；重新查询权威任务，不能假定已恢复事件。')
        frames = store.replay(p, after)

        async def stream():
            for event in frames:
                data = json.dumps(envelope(request, event['data']), ensure_ascii=False)
                yield f"id: {event['id']}\nevent: task\ndata: {data}\n\n"
            yield ': replay complete; reconnect for subsequent events\n\n'

        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})

    if dag:
        from .agent.dag.api import register_dag_routes
        register_dag_routes(app, dag, principal, envelope, errors)
    if orchestrator:
        from .agent.orchestration.api import register_agent_routes
        from .agent.orchestration.replay import RequestReplay
        register_agent_routes(app,orchestrator,principal,envelope,errors,RequestReplay() if enable_c4 else None)
    return app


app = create_app()
