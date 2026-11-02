"""Recheck regressions with an independent ledger; formal C gold stays untouched."""
import json
import sqlite3
from datetime import date
from pathlib import Path
import tempfile
import unittest

from demo.planner import local_plan
from demo.query import Ledger
from demo.semantic import IntentPlan


class RecheckQueriesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'ledger.sqlite'
        with sqlite3.connect(path) as conn:
            conn.executescript('CREATE TABLE accounts(id TEXT,user_id TEXT); CREATE TABLE transactions(id TEXT,user_id TEXT,account_id TEXT,status TEXT,posted_at TEXT,category TEXT,merchant TEXT,kind TEXT,direction TEXT,amount_cents INTEGER);')
            conn.execute("INSERT INTO accounts VALUES('a','u')")
            rows = [
                ('shopping-transfer', '购物', 'transfer', 'OUT', 2500),
                ('other-transfer', '其他', 'transfer', 'OUT', 1500),
                ('incoming-transfer', '其他', 'transfer', 'IN', 700),
                ('salary', '工资', 'income', 'IN', 10000),
                ('purchase', '购物', 'expense', 'OUT', 900),
                ('refund', '购物', 'refund', 'IN', 100),
                ('invalid-income', '工资', 'income', 'OUT', 90000),
                ('invalid-expense', '购物', 'expense', 'IN', 90000),
                ('invalid-refund', '购物', 'refund', 'OUT', 90000),
            ]
            for i, (ident, category, kind, direction, amount) in enumerate(rows):
                conn.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?,?)', (ident,'u','a','POSTED',f'2026-10-{26-i:02}',category,'商户',kind,direction,amount))
            conn.execute("INSERT INTO transactions VALUES('pending','u','a','PENDING','2026-10-27','工资','商户','income','IN',99999)")
            conn.execute("INSERT INTO transactions VALUES('foreign','v','a','POSTED','2026-10-27','工资','商户','income','IN',99999)")
        self.ledger = Ledger(path)

    def query(self, question):
        plan = IntentPlan.model_validate_json(json.dumps(local_plan(question, date(2026,10,26), ())['plan']))
        return plan, self.ledger.execute(plan, 'u')

    def test_transfer_type_is_independent_of_purpose_category(self):
        for question, expected in [('本月转账金额',4000), ('本月转账支出',4000), ('本月转账笔数',2), ('本月转账支出笔数',2), ('本月转账平均每笔金额',2000), ('本月转账收入',700)]:
            with self.subTest(question=question):
                plan, result = self.query(question)
                self.assertIsNone(plan.filters.category)
                self.assertEqual(result['rows'][0]['value_cents'], expected)

    def test_explicit_purpose_and_transfer_breakdown(self):
        _, result = self.query('本月购物转账支出')
        self.assertEqual(result['rows'][0]['value_cents'],2500)
        _, result = self.query('本月各类别转账支出')
        self.assertEqual(result['rows'], [{'group_key':'购物','value_cents':2500}, {'group_key':'其他','value_cents':1500}])
        _, result = self.query('本月转账记录')
        self.assertEqual(set(result['evidence_ids']), {'shopping-transfer','other-transfer','incoming-transfer'})

    def test_latest_transactions_include_income_and_only_valid_posted_activity(self):
        _, result = self.query('最近十笔交易')
        self.assertEqual(result['evidence_ids'], ['shopping-transfer','other-transfer','incoming-transfer','salary','purchase','refund'])
        self.assertEqual(result['rows'][3]['kind'],'income')

    def test_existing_transaction_count_contract_excludes_salary(self):
        _, result = self.query('本月交易笔数')
        self.assertEqual(result['rows'][0]['value_cents'],5)
