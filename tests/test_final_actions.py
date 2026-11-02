"""Account-bound transfer and planning continuation integration regressions."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date
from fastapi.testclient import TestClient
from demo.api import create_app
from demo.provider import ChatProvider


class FinalActionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / 'fixture.sqlite'
        shutil.copy2(Path(__file__).resolve().parents[1] / 'data/benchmark.sqlite', self.source)
        self.client = TestClient(create_app(db_path=self.source, provider=ChatProvider(provider='local', allow_local=True), today=date(2026,10,26)))
        self.addCleanup(self.client.close)
        self.sessions = {}
        for user in ('test_user', 'isolation_user'):
            self.client.post('/api/auth/register', json={'username':user,'password':'synthetic-final-password'}).raise_for_status()
            token = self.client.post('/api/auth/login', json={'username':user,'password':'synthetic-final-password'}).json()['auth_token']
            self.sessions[user] = self.client.post('/api/sessions',json={'auth_token':token}).json()['session_id']

    def ask(self, question, user='test_user'):
        return self.client.post('/api/ask',json={'session_id':self.sessions[user],'question':question}).json()

    def balances(self):
        return {r['account_id']:r['balance_cents'] for r in self.client.get('/api/accounts',params={'session_id':self.sessions['test_user']}).json()['accounts']}

    def test_explicit_savings_source_is_previewed_bound_and_debited_once(self):
        sid = self.sessions['test_user']; before = self.balances()
        preview = self.ask('从我的储蓄账户给测试用户B转25元')
        self.assertEqual(preview['status'], 'confirmation_required')
        self.assertIn('储蓄账户',preview['message'])
        aid = preview['action_id']
        auth = self.client.post(f'/api/actions/{aid}/authorize',json={'session_id':sid,'password':'synthetic-final-password'}).json()['authorization_id']
        body = {'session_id':sid,'authorization_id':auth,'category':'购物'}
        self.client.post(f'/api/actions/{aid}/confirm',json=body).raise_for_status()
        self.client.post(f'/api/actions/{aid}/confirm',json=body).raise_for_status()
        after = self.balances()
        self.assertEqual(after['acc_daily'],before['acc_daily'])
        self.assertEqual(after['acc_savings'],before['acc_savings']-2500)
        with sqlite3.connect(self.source.with_name('fixture_runtime.sqlite')) as db:
            rows=db.execute("SELECT account_id,direction FROM transactions WHERE id LIKE 'demo_%' AND user_id='test_user'").fetchall()
            self.assertEqual(rows,[('acc_savings','OUT')])

    def test_unknown_or_foreign_source_never_creates_action(self):
        for question in ('从我的工资账户给测试用户B转25元','从acc_other给测试用户B转25元','从储蓄账户和主账户给测试用户B转25元'):
            result=self.ask(question)
            self.assertIn(result['status'],('clarification','blocked'))
            self.assertNotIn('action_id',result)

    def test_completed_planning_accepts_parameters_without_hijacking_queries(self):
        self.assertEqual(self.ask('我想存6000元买电脑，至少留3000元')['status'],'simulated')
        for question, key, expected in [('至少留4000元','floor_cents',400000),('目标改为1000元','goal_cents',100000),('每天花100元','daily_budget_cents',10000)]:
            result=self.ask(question)
            self.assertEqual(result['status'],'simulated',question)
            self.assertEqual(result['result']['assumptions'][key],expected)
        self.assertEqual(self.ask('2026年9月退款是多少')['status'],'answer')
        self.assertEqual(self.ask('我的储蓄账户有多少钱？')['rows'][0]['balance_cents'],500000)
        self.assertEqual(self.ask('你好')['status'],'view')

    def test_account_clarification_can_be_confirmed_in_same_plan(self):
        self.ask('我想存6000元买电脑，至少留3000元')
        self.assertEqual(self.ask('用储蓄账户模拟未来30天余额')['status'],'clarification')
        result=self.ask('确认使用主账户')
        self.assertEqual(result['status'],'simulated')
        self.assertEqual(result['result']['assumptions']['opening_balance_cents'],900000)
        self.assertEqual(result['result']['assumptions']['goal_cents'],0)

    def test_incomplete_purchase_does_not_turn_into_zero_goal_on_floor_followup(self):
        self.assertEqual(self.ask('我想存钱买电脑')['status'],'clarification')
        result=self.ask('至少留3000元')
        self.assertEqual(result['status'],'clarification')
        result=self.ask('目标6000元')
        self.assertEqual(result['status'],'simulated')
        self.assertEqual(result['result']['assumptions']['goal_cents'],600000)
