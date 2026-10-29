"""Authenticated C3 entry points. Verification secrets are not chat fields."""

from fastapi import Depends, Request

from ..auth import Principal
from ..schemas import ChatRequest, Envelope
from ..dag.schemas import VersionRequest, ReplanRequest
from .schemas import ExecutionReport


def register_agent_routes(app, orchestrator, principal, envelope, errors):
    @app.post('/api/agent/chat',response_model=Envelope[ExecutionReport],responses=errors)
    async def chat(body:ChatRequest,request:Request,p:Principal=Depends(principal)):
        return envelope(request,await orchestrator.chat(body,p))

    @app.post('/api/agent/plans/{id}/run',response_model=Envelope[ExecutionReport],responses=errors)
    async def run(id:str,body:VersionRequest,request:Request,p:Principal=Depends(principal)):
        return envelope(request,await orchestrator.run(id,body.plan_version,p))

    @app.post('/api/agent/plans/{id}/replan',response_model=Envelope[ExecutionReport],responses=errors)
    async def replan(id:str,body:ReplanRequest,request:Request,p:Principal=Depends(principal)):
        return envelope(request,await orchestrator.replan(id,body,p))

    @app.post('/api/agent/plans/{id}/reconcile',response_model=Envelope[ExecutionReport],responses=errors)
    async def reconcile(id:str,body:VersionRequest,request:Request,p:Principal=Depends(principal)):
        return envelope(request,await orchestrator.reconcile(id,body.plan_version,p))

    @app.post('/api/agent/plans/{id}/cancel',response_model=Envelope[ExecutionReport],responses=errors)
    async def cancel(id:str,body:VersionRequest,request:Request,p:Principal=Depends(principal)):
        return envelope(request,await orchestrator.cancel(id,body.plan_version,p))

    @app.get('/api/agent/plans/{id}/report',response_model=Envelope[ExecutionReport],responses=errors)
    async def report(id:str,request:Request,p:Principal=Depends(principal)):
        return envelope(request,orchestrator.report(id,p))

    @app.get('/api/agent/tools',response_model=Envelope[list[dict]],responses=errors)
    async def catalog(request:Request,p:Principal=Depends(principal)):
        return envelope(request,orchestrator.runtime.registry.catalog())
