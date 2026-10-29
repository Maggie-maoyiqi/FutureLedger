"""Business/legacy B replay guards; all financial endpoints here are Mock B."""

import unittest

from c4_support import Harness, ContractGate, TRANSFER_INTENT
from test_dag_c2 import transfer_nodes, node


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h=Harness()
        self.addCleanup(self.h.close)

    def test_repeated_chat_key_returns_one_plan_and_one_b_proposal(self):
        headers={**self.h.headers,'Idempotency-Key':'c4_same_request'}
        first=self.h.transfer(headers).json()
        second=self.h.transfer(headers).json()
        self.assertEqual(first['data']['plan_id'],second['data']['plan_id'])
        self.assertEqual(len(self.h.b.proposals),1)
        changed=self.h.request('/api/agent/chat',{'session_id':self.h.sid,'text':'不同请求'},headers=headers)
        self.assertEqual(changed.status_code,409)
        self.assertEqual(changed.json()['error']['code'],'IDEMPOTENCY_CONFLICT')

    def test_successful_financial_branch_is_not_reported_as_wholly_failed(self):
        self.h.b.outcome='SUCCEEDED'
        from services.api.agent.errors import AgentError
        async def unavailable(*args): raise AgentError('UPSTREAM_UNAVAILABLE',503)
        self.h.d.execute_analysis_plan=unavailable
        plan=dict(kind='analysis',dataset_id='personal_ledger',semantic_version=1,metric='net_expense',
            start='2026-09-01',end_exclusive='2026-10-01',filters={'category':'餐饮'},group_by=None,limit=100)
        new=self.h.post('/api/dag/plans',{'session_id':self.h.sid,'nodes':transfer_nodes()+[
            node('zfailure','analytics.analysis',{'plan':plan})]})['data']
        self.h.post('/api/dag/plans/'+new['plan_id']+'/run',{'plan_version':1})
        self.h.post('/api/dag/plans/'+new['plan_id']+'/operations',{'plan_version':1,'node_id':'payment'})
        report=self.h.post('/api/agent/plans/'+new['plan_id']+'/run',{'plan_version':1})['data']
        self.assertEqual(report['node_statuses']['payment'],'SUCCEEDED')
        self.assertEqual(report['node_statuses']['zfailure'],'FAILED')
        self.assertEqual(report['execution_status'],'PARTIAL')


class LegacyBTests(unittest.TestCase):
    def setUp(self):
        self.b=ContractGate()
        self.h=Harness(execution=self.b)
        self.addCleanup(self.h.close)
        chat=self.h.post('/api/chat',{'session_id':self.h.sid,'text':'给妈妈转25元'})['data']
        self.plan_id=chat['plan_id']
        created=self.h.post('/api/operations',{'plan_id':self.plan_id,'plan_version':1,'selected_action':'propose_transfer'})['data']
        self.id=created['operation_id']
        self.base='/api/operations/'+self.id

    def authorize(self):
        self.assertEqual(self.h.request(self.base+'/confirm',{'plan_version':1,'params_hash':'mock_params_hash'}).status_code,200)
        self.assertEqual(self.h.request(self.base+'/verify',{'credential':'C4_SYNTHETIC_OTP_MARKER'}).status_code,200)

    def test_explicit_b_confirm_verify_execute_and_status_contract(self):
        self.authorize()
        r=self.h.request(self.base+'/execute',{'plan_version':1},headers={**self.h.headers,'Idempotency-Key':'mock_execute'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['data']['status'],'SUCCEEDED')

    def test_unconfirmed_execute_never_reaches_gate(self):
        r=self.h.request(self.base+'/execute',{'plan_version':1},headers={**self.h.headers,'Idempotency-Key':'mock_unconfirmed'})
        self.assertIn(r.status_code,(401,409))
        self.assertEqual(self.b.execute_calls,0)

    def test_wrong_owner_and_stale_version_rejected_before_b(self):
        r=self.h.request(self.base+'/execute',{'plan_version':1},headers={'Authorization':'Bearer bob','Idempotency-Key':'wrong_owner'})
        self.assertEqual(r.status_code,403)
        r=self.h.request(self.base+'/execute',{'plan_version':2},headers={**self.h.headers,'Idempotency-Key':'wrong_version'})
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.b.execute_calls,0)

    def test_lost_execution_response_does_not_resubmit(self):
        self.authorize()
        self.b.lost_response=True
        headers={**self.h.headers,'Idempotency-Key':'same_execute'}
        first=self.h.request(self.base+'/execute',{'plan_version':1},headers=headers)
        self.assertEqual(first.status_code,409,first.text)
        self.assertEqual(first.json()['error']['code'],'PENDING_UNKNOWN')
        second=self.h.request(self.base+'/execute',{'plan_version':1},headers=headers)
        self.assertEqual(second.status_code,409)
        self.assertEqual(self.b.execute_calls,1)
        status=self.h.client.get(self.base,headers=self.h.headers)
        self.assertEqual(status.json()['data']['status'],'SUCCEEDED')

    def test_stale_b_read_cannot_erase_success_and_enable_second_execute(self):
        self.authorize()
        headers={**self.h.headers,'Idempotency-Key':'terminal'}
        self.assertEqual(self.h.request(self.base+'/execute',{'plan_version':1},headers=headers).status_code,200)
        self.b.operations[self.id]['status']='READY'
        self.assertEqual(self.h.client.get(self.base,headers=self.h.headers).json()['data']['status'],'SUCCEEDED')
        self.assertEqual(self.h.request(self.base+'/execute',{'plan_version':1},headers=headers).json()['data']['status'],'SUCCEEDED')
        self.assertEqual(self.b.execute_calls,1)

    def test_stale_ready_after_lost_response_does_not_allow_resubmit(self):
        self.authorize()
        self.b.lost_response=True
        headers={**self.h.headers,'Idempotency-Key':'lost_then_stale'}
        self.h.request(self.base+'/execute',{'plan_version':1},headers=headers)
        self.b.operations[self.id]['status']='READY'
        self.h.client.get(self.base,headers=self.h.headers)
        response=self.h.request(self.base+'/execute',{'plan_version':1},headers=headers)
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.b.execute_calls,1)
