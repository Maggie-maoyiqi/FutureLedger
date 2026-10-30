"""Sixteen fault cases; restart test proves state loss, not successful recovery."""

import asyncio
import unittest

import httpx

from c4_support import Harness, TRANSFER_INTENT
from test_dag_c2 import node
from services.api.agent.errors import AgentError


class FaultTests(unittest.TestCase):
    def setUp(self):
        self.h=Harness()
        self.addCleanup(self.h.close)

    def test_01_llm_timeout(self):
        self.h.provider.error=httpx.ReadTimeout('PRIVATE_DETAIL')
        self.assertEqual(self.h.transfer().status_code,503)
        self.assertEqual(self.h.b.proposals,[])

    def test_02_llm_invalid_json(self):
        self.h.provider.result='{invalid'
        self.assertEqual(self.h.transfer().status_code,502)

    def test_03_llm_invalid_schema(self):
        self.h.provider.result={**TRANSFER_INTENT,'permissions':['execute']}
        self.assertEqual(self.h.transfer().status_code,502)
        self.assertEqual(self.h.a.calls,[])

    def test_04_provider_5xx_is_finitely_retried(self):
        response=httpx.Response(503,request=httpx.Request('POST','https://mock.test'))
        self.h.provider.error=httpx.HTTPStatusError('PRIVATE_BODY',request=response.request,response=response)
        r=self.h.transfer()
        self.assertEqual(r.status_code,503)
        self.assertEqual(len(self.h.provider.inputs),2)
        self.assertNotIn('PRIVATE_BODY',r.text)

    def test_05_d_unavailable(self):
        from test_agent_c1 import intent
        async def unavailable(*args): raise AgentError('UPSTREAM_UNAVAILABLE',503)
        self.h.d.execute_analysis_plan=unavailable
        plan=dict(kind='analysis',dataset_id='personal_ledger',semantic_version=1,metric='net_expense',
            start='2026-09-01',end_exclusive='2026-10-01',filters={'category':'餐饮'},group_by=None,limit=100)
        self.h.provider.result=intent('analysis',analysis_plan=plan)
        report=self.h.transfer().json()['data']
        self.assertEqual(report['node_statuses']['analysis'],'FAILED')

    def test_06_bank_read_timeout(self):
        self.h.a.failures=99
        report=self.h.transfer().json()['data']
        self.assertEqual(report['node_statuses']['balance'],'FAILED')
        self.assertEqual(self.h.a.calls.count('get_balance'),2)
        self.assertEqual(self.h.b.proposals,[])

    def test_07_b_creation_failure_is_unknown_without_retry(self):
        calls=[]
        async def fail(*args):
            calls.append(1)
            raise httpx.ReadTimeout('lost_prepare')
        self.h.b.create_operation=fail
        report=self.h.transfer().json()['data']
        self.assertEqual(report['execution_status'],'PENDING_UNKNOWN')
        self.h.post('/api/agent/plans/'+report['plan_id']+'/run',{'plan_version':1})
        self.assertEqual(len(calls),1)

    def test_08_unknown_tool_status_never_becomes_success(self):
        report=self.h.transfer().json()['data']
        async def unknown(p,binding):
            return {**binding.model_dump(),'status':'MAGIC_SUCCESS','parameters':TRANSFER_INTENT['entities'],'bank_ref':'mock'}
        self.h.b.get_operation_status=unknown
        next_report=self.h.post('/api/agent/plans/'+report['plan_id']+'/reconcile',{'plan_version':1})['data']
        self.assertEqual(next_report['execution_status'],'PENDING_UNKNOWN')

    def test_09_one_node_failure_blocks_downstream(self):
        self.h.a.inject=True
        report=self.h.transfer().json()['data']
        self.assertEqual(report['node_statuses']['balance'],'FAILED')
        self.assertEqual(report['node_statuses']['payment'],'BLOCKED')

    def test_10_independent_partial_success_is_preserved(self):
        self.h.a.inject=True
        graph=self.h.post('/api/dag/plans',{'session_id':self.h.sid,'nodes':[node('a'),
            node('recipient','bank.recipient',{'source_account_id':'acc_daily','recipient_id':'rec_1'})]})['data']
        report=self.h.post('/api/agent/plans/'+graph['plan_id']+'/run',{'plan_version':1})['data']
        self.assertEqual(report['node_statuses'],{'a':'FAILED','recipient':'SUCCEEDED'})

    def test_11_user_cancellation_blocks_future_dispatch(self):
        report=self.h.transfer().json()['data']
        self.h.post('/api/agent/plans/'+report['plan_id']+'/cancel',{'plan_version':1})
        self.assertEqual(self.h.request('/api/agent/plans/'+report['plan_id']+'/run',{'plan_version':1}).status_code,409)

    def test_12_salary_change_replans_only_affected_branch(self):
        graph=self.h.post('/api/dag/plans',{'session_id':self.h.sid,'nodes':[node('balance'),
            node('forecast','cashflow.simulate',{'source_account_id':'acc_daily','amount_cents':600000,
                'target_date':'2026-12-01','salary_date':'2026-11-12'},('balance',))]})['data']
        self.h.post('/api/agent/plans/'+graph['plan_id']+'/run',{'plan_version':1})
        report=self.h.post('/api/agent/plans/'+graph['plan_id']+'/replan',{'plan_version':1,
            'reason':'user_changed','updates':{'salary_date':'2026-11-17'}})['data']
        self.assertEqual(report['plan_version'],2)
        self.assertFalse(report['outputs']['forecast']['feasible'])
        self.assertEqual(self.h.a.calls.count('get_balance'),1)

    def test_13_successful_write_is_never_proposed_again(self):
        self.h.b.outcome='SUCCEEDED'
        report=self.h.transfer().json()['data']
        report=self.h.post('/api/agent/plans/'+report['plan_id']+'/replan',{'plan_version':1,
            'reason':'user_changed','updates':{'amount_cents':9999}})['data']
        self.assertEqual(len(self.h.b.proposals),1)
        self.assertEqual(report['outputs']['payment']['parameters']['amount_cents'],2500)

    def test_14_submitted_response_loss_only_queries(self):
        report=self.h.transfer().json()['data']
        self.h.b.outcome='PENDING_UNKNOWN'
        unknown=self.h.post('/api/agent/plans/'+report['plan_id']+'/reconcile',{'plan_version':1})['data']
        self.assertEqual(unknown['execution_status'],'PENDING_UNKNOWN')
        self.h.b.outcome='SUCCEEDED'
        recovered=self.h.post('/api/agent/plans/'+report['plan_id']+'/reconcile',{'plan_version':1})['data']
        self.assertEqual(recovered['execution_status'],'MOCK_COMPLETED')
        self.assertEqual(self.h.b.execute_calls,0)

    def test_15_client_replay_does_not_duplicate_proposal(self):
        headers={**self.h.headers,'Idempotency-Key':'repeated_mock'}
        ids=[self.h.transfer(headers).json()['data']['plan_id'] for _ in range(3)]
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(len(self.h.b.proposals),1)

    def test_16_fresh_instance_has_no_durable_task_recovery(self):
        report=self.h.transfer().json()['data']
        fresh=Harness()
        self.addCleanup(fresh.close)
        response=fresh.client.get('/api/agent/plans/'+report['plan_id']+'/report',headers=fresh.headers)
        self.assertEqual(response.status_code,403)
        self.assertEqual(fresh.request('/api/agent/chat',{'session_id':self.h.sid,'text':'重试'}).status_code,403)
        self.assertEqual(fresh.b.proposals,[])


if __name__=='__main__': unittest.main()
