"""Isolated test adapters; never a bank implementation or production authority."""

from datetime import date

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.agent.auth import DevelopmentTokens
from services.api.agent.errors import AgentError, forbidden
from services.api.agent.ports import OperationState, UnavailableExecution
from test_agent_c1 import FakeProvider, intent
from test_dag_c2 import P, OTHER
from c3_fakes import FakeBankRead, FakeGate, FakeAnalytics


TODAY=date(2026,10,29)
TRANSFER_INTENT=intent('transfer',amount_cents=2500,recipient_id='rec_1',
    source_account_id='acc_daily',execution_date='2026-11-02')


class Harness:
    def __init__(self, **kwargs):
        self.a,self.b,self.d=FakeBankRead(),FakeGate(),FakeAnalytics()
        self.provider=FakeProvider(TRANSFER_INTENT)
        self.auth=DevelopmentTokens({'alice':P,'bob':OTHER})
        self.app=create_app(authenticator=self.auth,bank_read_adapter=self.a,execution_gate_adapter=self.b,
            analytics_adapter=self.d,agent_provider=self.provider,clock=lambda:TODAY,**kwargs)
        self.client=TestClient(self.app)
        self.client.__enter__()
        self.headers={'Authorization':'Bearer alice'}
        self.sid=self.post('/api/sessions',{})['data']['session_id']

    def close(self): self.client.__exit__(None,None,None)

    def post(self,path,body,**kwargs):
        response=self.client.post(path,json=body,headers=kwargs.pop('headers',self.headers),**kwargs)
        return response.json()

    def request(self,path,body,**kwargs):
        return self.client.post(path,json=body,headers=kwargs.pop('headers',self.headers),**kwargs)

    def transfer(self,headers=None):
        return self.request('/api/agent/chat',{'session_id':self.sid,'text':'给妈妈转25元，2026-11-02'},
            headers=headers or self.headers)


class ContractGate(UnavailableExecution):
    """Test-only B state contract: no ledger, funds or real authentication."""
    provenance='mock_B'

    def __init__(self):
        self.operations={}
        self.execute_calls=0
        self.lost_response=False

    def _get(self,p,id):
        item=self.operations.get(id)
        if item is None or item['owner']!=p.subject: raise forbidden()
        return item

    def _view(self,id,item):
        return OperationState(operation_id=id,plan_id=item['plan_id'],plan_version=1,
            status=item['status'],bank_ref='mock_bank_ref' if item['status']=='SUCCEEDED' else None,
            next_action='Mock contract only')

    async def propose(self,p,snapshot,action):
        id='c4_mock_op_'+str(len(self.operations)+1)
        item=dict(owner=p.subject,plan_id=snapshot.plan_id,status='NEEDS_CONFIRM')
        self.operations[id]=item
        return self._view(id,item)

    async def confirm(self,p,id,command):
        item=self._get(p,id)
        if command.plan_version!=1: raise AgentError('PLAN_STALE',409)
        if command.params_hash!='mock_params_hash': raise AgentError('PLAN_STALE',409)
        item['status']='NEEDS_AUTH'
        return self._view(id,item)

    async def verify(self,p,id,command):
        item=self._get(p,id)
        if item['status']!='NEEDS_AUTH': raise AgentError('AUTH_REQUIRED',401)
        if command.credential.get_secret_value()!='C4_SYNTHETIC_OTP_MARKER':
            raise AgentError('AUTH_REQUIRED',401)
        item['status']='READY'
        return self._view(id,item)

    async def execute(self,p,id,command,idempotency_key):
        import httpx
        item=self._get(p,id)
        self.execute_calls+=1
        if item['status']=='SUCCEEDED': return self._view(id,item)
        if item['status']!='READY': raise AgentError('AUTH_REQUIRED',401)
        item['status']='SUCCEEDED'
        if self.lost_response: raise httpx.ReadTimeout('C4_PRIVATE_UPSTREAM_DETAIL')
        return self._view(id,item)

    async def get(self,p,id): return self._view(id,self._get(p,id))
