"""C1 -> C2 API integration with explicit fake providers and mock A/B/D."""

import unittest
import asyncio

import httpx

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.agent.auth import DevelopmentTokens
from services.api.agent.planner import ModelPlanner
from test_agent_c1 import FakeProvider, intent
from test_dag_c2 import MockB, MockTools, OTHER, P, node, transfer_nodes


class DagApiTests(unittest.TestCase):
    def setUp(self):
        self.tools, self.b = MockTools(), MockB()
        self.client = TestClient(create_app(authenticator=DevelopmentTokens({'alice': P, 'bob': OTHER}),
            dag_tools=self.tools, dag_execution=self.b))
        self.headers = {'Authorization': 'Bearer alice'}
        self.sid = self.post('/api/sessions', {'dataset_id': 'personal_ledger'})['session_id']

    def tearDown(self):
        self.client.close()

    def post(self, path, payload, expected=200):
        r = self.client.post(path, json=payload, headers=self.headers)
        self.assertEqual(r.status_code, expected, r.text)
        body = r.json()
        self.assertEqual(set(body), {'data', 'error', 'request_id'})
        return body['data'] if expected == 200 else body['error']

    def create(self):
        return self.post('/api/dag/plans', {'session_id': self.sid, 'nodes': transfer_nodes()})

    def test_c1_transfer_promotion_and_legacy_proposal_bypass_blocked(self):
        chat = self.post('/api/chat', {'session_id': self.sid, 'text': '从acc_daily给妈妈转25元，明天'})
        dag = self.post('/api/plans/' + chat['plan_id'] + '/dag', {'plan_version': 1})
        self.assertEqual(dag['origin_c1_plan_id'], chat['plan_id'])
        self.assertEqual(dag['nodes'][-1]['tool_name'], 'execution.transfer')
        error = self.post('/api/operations', {'plan_id': chat['plan_id'], 'plan_version': 1,
                         'selected_action': 'propose_transfer'}, 409)
        self.assertEqual(error['code'], 'PLAN_STALE')

    def test_preexisting_c1_operations_block_promotion_without_invalidation_protocol(self):
        from services.api.agent.ports import OperationState, UnavailableExecution
        class LegacyGateway(UnavailableExecution):
            async def propose(self, principal, snapshot, action):
                return OperationState(operation_id='legacy_mock_op', plan_id=snapshot.plan_id,
                    plan_version=snapshot.plan_version, status='DRAFT', next_action='Mock proposal only')
        with TestClient(create_app(authenticator=DevelopmentTokens({'alice': P}), execution=LegacyGateway())) as c:
            sid = c.post('/api/sessions', json={}, headers=self.headers).json()['data']['session_id']
            chat = c.post('/api/chat', json={'session_id': sid, 'text': '从acc_daily给妈妈转25元，明天'}, headers=self.headers).json()['data']
            r = c.post('/api/operations', json={'plan_id': chat['plan_id'], 'plan_version': 1,
                       'selected_action': 'propose_transfer'}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            r = c.post('/api/plans/' + chat['plan_id'] + '/dag', json={'plan_version': 1}, headers=self.headers)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()['error']['code'], 'LEGACY_OPERATIONS_EXIST')

    def test_read_only_c1_template_contains_no_write_node(self):
        chat = self.post('/api/chat', {'session_id': self.sid, 'text': '先不要转，只看看，从acc_daily给妈妈转25元，明天'})
        dag = self.post('/api/plans/' + chat['plan_id'] + '/dag', {'plan_version': 1})
        self.assertFalse(any(n['tool_name'].startswith('execution.') for n in dag['nodes']))

    def test_fake_provider_intent_enters_same_validated_dag_path(self):
        provider = FakeProvider(intent('transfer', source_account_id='acc_daily', recipient_id='rec_1',
                    amount_cents=2500, execution_date='2026-11-02'))
        with TestClient(create_app(authenticator=DevelopmentTokens({'alice': P}), planner=ModelPlanner(provider))) as c:
            sid = c.post('/api/sessions', json={}, headers=self.headers).json()['data']['session_id']
            chat = c.post('/api/chat', json={'session_id': sid, 'text': '准备转账'}, headers=self.headers).json()['data']
            r = c.post('/api/plans/' + chat['plan_id'] + '/dag', json={'plan_version': 1}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['data']['nodes'][-1]['risk_level'], 'B_REQUIRED')

    def test_graph_validation_errors_unknown_tool_cycle_and_scope(self):
        for nodes in ([node('a', tool='bank.execute')], [node('a', deps=('b',)), node('b', deps=('a',))]):
            error = self.post('/api/dag/plans', {'session_id': self.sid, 'nodes': nodes}, 422)
            self.assertEqual(error['code'], 'INVALID_REQUEST')
        error = self.post('/api/dag/plans', {'session_id': self.sid,
                         'nodes': [node('a', inputs={'source_account_id': 'acc_other'})]}, 403)
        self.assertEqual(error['code'], 'FORBIDDEN')

    def test_replan_api_blocks_old_version_and_records_diff(self):
        first = self.create()
        path = '/api/dag/plans/' + first['plan_id']
        self.post(path + '/run', {'plan_version': 1})
        second = self.post(path + '/replan', {'plan_version': 1, 'reason': 'user_changed', 'updates': {'amount_cents': 3000}})
        self.assertEqual(second['plan_version'], 2)
        self.assertEqual(second['diff']['affected_nodes'], ['payment', 'quote'])
        self.assertEqual(self.post(path + '/run', {'plan_version': 1}, 409)['code'], 'PLAN_STALE')
        old = self.client.get(path, params={'version': 1}, headers=self.headers).json()['data']
        self.assertEqual(old['state'], 'SUPERSEDED')

    def test_old_bound_operations_cannot_use_c1_confirm_verify_execute(self):
        first = self.create()
        path = '/api/dag/plans/' + first['plan_id']
        self.post(path + '/run', {'plan_version': 1})
        op = self.post(path + '/operations', {'plan_version': 1, 'node_id': 'payment'})
        self.post(path + '/replan', {'plan_version': 1, 'reason': 'user_changed', 'updates': {'amount_cents': 3000}})
        for action, payload in [('confirm', {'plan_version': 1, 'params_hash': 'old'}),
                                ('verify', {'credential': 'old-fake-secret'}), ('execute', {'plan_version': 1})]:
            r = self.client.post('/api/operations/' + op['operation_id'] + '/' + action,
                json=payload, headers={**self.headers, 'Idempotency-Key': 'old-fake-key'})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()['error']['code'], 'PLAN_STALE')

    def test_cross_subject_plans_history_results_and_events_are_isolated(self):
        first = self.create()
        path = '/api/dag/plans/' + first['plan_id']
        ran = self.post(path + '/run', {'plan_version': 1})
        bob = {'Authorization': 'Bearer bob'}
        for suffix in ('', '/results/' + ran['nodes'][0]['result_ref']):
            r = self.client.get(path + suffix, headers=bob)
            self.assertEqual(r.status_code, 403, r.text)
        r = self.client.get('/api/events', headers=bob)
        self.assertNotIn(first['plan_id'], r.text)
        self.assertIn(first['plan_id'], self.client.get('/api/events', headers=self.headers).text)

    def test_api_results_cannot_be_used_to_submit_authority(self):
        first = self.create()
        path = '/api/dag/plans/' + first['plan_id']
        self.assertEqual(self.post(path + '/run', {'plan_version': 1, 'authorized': True}, 422)['code'], 'INVALID_REQUEST')
        self.assertEqual(self.post(path + '/replan', {'plan_version': 1, 'reason': 'user_changed',
                         'updates': {'amount_cents': 3000}, 'auth_ref': 'old'}, 422)['code'], 'INVALID_REQUEST')

    def test_default_adapters_are_explicitly_unsupported_not_mock_success(self):
        with TestClient(create_app(authenticator=DevelopmentTokens({'alice': P}))) as c:
            sid = c.post('/api/sessions', json={}, headers=self.headers).json()['data']['session_id']
            created = c.post('/api/dag/plans', json={'session_id': sid, 'nodes': [node('a')]}, headers=self.headers).json()['data']
            r = c.post('/api/dag/plans/' + created['plan_id'] + '/run', json={'plan_version': 1}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['data']['nodes'][0]['status'], 'FAILED')
            self.assertEqual(r.json()['data']['nodes'][0]['error_code'], 'UNSUPPORTED')

    def test_openapi_exposes_security_and_c2_models(self):
        schema = self.client.get('/openapi.json').json()
        for path in ('/api/dag/plans', '/api/dag/plans/{id}/replan', '/api/plans/{id}/dag'):
            self.assertTrue(schema['paths'][path]['post']['security'])
        self.assertIn('DagPlan', schema['components']['schemas'])


class LegacyRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_c1_proposal_cannot_race_dag_promotion(self):
        from services.api.agent.ports import OperationState, UnavailableExecution
        entered, release = asyncio.Event(), asyncio.Event()
        class DelayedLegacy(UnavailableExecution):
            async def propose(self, principal, snapshot, action):
                entered.set()
                await release.wait()
                return OperationState(operation_id='legacy_mock_inflight', plan_id=snapshot.plan_id,
                    plan_version=snapshot.plan_version, status='DRAFT', next_action='Mock proposal only')
        app = create_app(authenticator=DevelopmentTokens({'alice': P}), execution=DelayedLegacy())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test',
                                     headers={'Authorization': 'Bearer alice'}) as c:
            sid = (await c.post('/api/sessions', json={})).json()['data']['session_id']
            chat = (await c.post('/api/chat', json={'session_id': sid,
                'text': '从acc_daily给妈妈转25元，明天'})).json()['data']
            task = asyncio.create_task(c.post('/api/operations', json={'plan_id': chat['plan_id'],
                'plan_version': 1, 'selected_action': 'propose_transfer'}))
            await entered.wait()
            try:
                r = await c.post('/api/plans/' + chat['plan_id'] + '/dag', json={'plan_version': 1})
                self.assertEqual(r.status_code, 409, r.text)
                self.assertEqual(r.json()['error']['code'], 'LEGACY_OPERATIONS_EXIST')
            finally:
                release.set()
            self.assertEqual((await task).status_code, 200)


if __name__ == '__main__':
    unittest.main()
