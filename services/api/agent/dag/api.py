"""Additive authenticated C2 API. No confirmation credentials in DAG requests."""

from fastapi import Depends, Query, Request

from ..auth import Principal
from ..schemas import Envelope
from .schemas import DagCreate, DagPlan, PrepareRequest, PreparedOperation, ReplanRequest, ResultRecord, VersionRequest


def register_dag_routes(app, engine, principal, envelope, errors):
    @app.post('/api/dag/plans', response_model=Envelope[DagPlan], responses=errors)
    async def create(body: DagCreate, request: Request, p: Principal = Depends(principal)):
        return envelope(request, engine.create(body, p))

    @app.post('/api/plans/{id}/dag', response_model=Envelope[DagPlan], responses=errors)
    async def promote(id: str, body: VersionRequest, request: Request, p: Principal = Depends(principal)):
        return envelope(request, engine.from_c1(id, body.plan_version, p))

    @app.get('/api/dag/plans/{id}', response_model=Envelope[DagPlan], responses=errors)
    async def get(id: str, request: Request, version: int | None = Query(default=None, ge=1),
                  p: Principal = Depends(principal)):
        return envelope(request, engine.get(id, p, version))

    @app.post('/api/dag/plans/{id}/run', response_model=Envelope[DagPlan], responses=errors)
    async def run(id: str, body: VersionRequest, request: Request, p: Principal = Depends(principal)):
        return envelope(request, await engine.run(id, body.plan_version, p))

    @app.post('/api/dag/plans/{id}/reconcile', response_model=Envelope[DagPlan], responses=errors)
    async def reconcile(id: str, body: VersionRequest, request: Request, p: Principal = Depends(principal)):
        return envelope(request, await engine.reconcile(id, body.plan_version, p))

    @app.post('/api/dag/plans/{id}/replan', response_model=Envelope[DagPlan], responses=errors)
    async def replan(id: str, body: ReplanRequest, request: Request, p: Principal = Depends(principal)):
        return envelope(request, await engine.replan(id, body, p))

    @app.post('/api/dag/plans/{id}/operations', response_model=Envelope[PreparedOperation], responses=errors)
    async def prepare(id: str, body: PrepareRequest, request: Request, p: Principal = Depends(principal)):
        return envelope(request, await engine.prepare(id, body.plan_version, body.node_id, p))

    @app.get('/api/dag/plans/{id}/results/{result_ref}', response_model=Envelope[ResultRecord], responses=errors)
    async def result(id: str, result_ref: str, request: Request, p: Principal = Depends(principal)):
        return envelope(request, engine.result(id, result_ref, p))
