"""Regression contracts for named accounts and shared live planning balances."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date
from fastapi.testclient import TestClient
from demo.api import create_app
from demo.provider import ChatProvider


class RecheckApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / 'fixture.sqlite'
        shutil.copy2(Path(__file__).resolve().parents[1] / 'data/benchmark.sqlite', self.source)
        self.client = TestClient(create_app(db_path=self.source, provider=ChatProvider(provider='local', allow_local=True), today=date(2026, 10, 26)))
        self.addCleanup(self.client.close)
        self.sessions = {}
        for user in ('test_user', 'isolation_user'):
            self.client.post('/api/auth/register', json={'username': user, 'password': 'synthetic-recheck-password'}).raise_for_status()
            auth = self.client.post('/api/auth/login', json={'username': user, 'password': 'synthetic-recheck-password'}).json()['auth_token']
            self.sessions[user] = self.client.post('/api/sessions', json={'auth_token': auth}).json()['session_id']

    def ask(self, question, user='test_user'):
        return self.client.post('/api/ask', json={'session_id': self.sessions[user], 'question': question}).json()

    def test_named_unknown_account_cannot_fall_back_to_all_accounts(self):
        for question in ('我的信用卡账户余额是多少？', '我的工资账户余额是多少？', '查看不存在账户的余额', '我的招行卡余额是多少？', 'my checking account balance'):
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertIn(result['status'], ('clarification', 'blocked'))
                self.assertNotIn('rows', result)
        self.assertEqual(len(self.ask('我的账户列表和各自余额')['rows']), 2)
        self.assertEqual(self.ask('我的储蓄账户有多少钱？')['rows'][0]['balance_cents'], 500000)

    def test_both_planning_entries_use_current_scoped_balance_after_transfer(self):
        sid = self.sessions['test_user']
        action = self.ask('给测试用户B转25元')['action_id']
        authorization = self.client.post(f'/api/actions/{action}/authorize', json={'session_id': sid, 'password': 'synthetic-recheck-password'}).json()['authorization_id']
        self.client.post(f'/api/actions/{action}/confirm', json={'session_id': sid, 'authorization_id': authorization, 'category': '购物'}).raise_for_status()
        for question in ('本月转账金额', '本月转账支出'):
            self.assertEqual(self.ask(question)['rows'], [{'value_cents': 2500}])
        self.assertEqual(self.ask('本月转账有几笔')['rows'], [{'value_cents': 1}])
        self.assertEqual(self.ask('本月各类别转账支出')['rows'], [{'group_key': '购物', 'value_cents': 2500}])
        self.assertEqual(len(self.ask('本月转账记录')['rows']), 1)
        self.assertEqual(self.ask('本月转账平均每笔金额是多少')['rows'], [{'value_cents': 2500.0}])
        for user, expected in [('test_user', 897500), ('isolation_user', 702500)]:
            with self.subTest(user=user):
                manual = self.client.post('/api/plans/demo-goal/simulate', json={'session_id': self.sessions[user], 'plan_version': 1, 'salary_day': 15, 'purchase_day': 30}).json()
                chat = self.ask('我想存6000元买电脑，至少留3000元', user)
                self.assertEqual(manual['result']['assumptions']['opening_balance_cents'], expected)
                self.assertEqual(manual['result']['points_cents'], chat['result']['points_cents'])
        with sqlite3.connect(self.source.with_name('fixture_runtime.sqlite')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_transfers').fetchone()[0], 1)

    def test_full_activity_metric_is_formatted_as_a_count(self):
        from demo.api import display_value
        self.assertEqual(display_value(2, 'activity_count'), '2 笔')

    def test_pure_projection_and_salary_delay_reach_simulation(self):
        for question in ('模拟未来30天余额，不要改账本', '如果工资晚五天到账，还能不能保留3000元？'):
            result = self.ask(question)
            self.assertEqual(result['status'], 'simulated')
            self.assertEqual(result['result']['assumptions']['goal_cents'], 0)
            self.assertEqual(len(result['result']['points_cents']), 30)
        self.assertEqual(self.ask('我想存6000元买电脑，至少留3000元')['status'], 'simulated')
        result = self.ask('模拟未来30天余额，不要改账本')
        self.assertEqual(result['result']['assumptions']['goal_cents'], 0)

    def test_polite_and_multi_account_balance_requests_keep_their_scope(self):
        for question in ('能告诉我账户余额吗？', '主账户和储蓄账户分别有多少钱？', '麻烦显示我的余额，谢谢', 'show me my account balance'):
            result = self.ask(question)
            self.assertEqual(result['status'], 'answer', question)
            self.assertEqual(len(result['rows']), 2)
