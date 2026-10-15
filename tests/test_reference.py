import csv
import hashlib
import json
import sqlite3
import sys
import unittest
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from reference_engine import query,simulate


class DataIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT/'data/transactions.csv').open() as f: cls.rows=list(csv.DictReader(f))

    def test_source_hashes(self):
        manifest=json.loads((ROOT/'data/manifest.json').read_text())
        for name,expected in manifest['files'].items():
            self.assertEqual(hashlib.sha256((ROOT/'data'/name).read_bytes()).hexdigest(),expected,name)

    def test_raw_import_deduplication(self):
        raw=[json.loads(s) for s in (ROOT/'data/raw_imports.jsonl').read_text().splitlines()]
        keys={(r['account_id'],r['external_id']) for r in raw}
        self.assertEqual(len(raw)-len(keys),124)
        self.assertEqual(len(keys),len(self.rows))
        distinct=[r for r in self.rows if r['merchant']=='模拟可疑重复商户']
        self.assertEqual(len(distinct),2)
        self.assertNotEqual(distinct[0]['external_id'],distinct[1]['external_id'])

    def test_reconcile_each_account_and_nonnegative_history(self):
        for a in json.loads((ROOT/'data/accounts.json').read_text()):
            balance=a['opening_balance_cents']
            self.assertGreaterEqual(balance,0)
            for r in self.rows:
                if r['account_id']==a['id'] and r['status']=='POSTED':
                    balance+=(1 if r['direction']=='IN' else -1)*int(r['amount_cents'])
                    self.assertGreaterEqual(balance,0,r['id'])
            self.assertEqual(balance,a['balance_cents'])

    def test_refund_reference_and_category(self):
        by_id={r['id']:r for r in self.rows}
        for r in self.rows:
            if r['kind']=='refund':
                original=by_id[r['refund_of']]
                self.assertEqual(original['user_id'],r['user_id'])
                self.assertEqual(original['category'],r['category'])
                self.assertEqual(original['account_id'],r['account_id'])
                self.assertLessEqual(int(r['amount_cents']),int(original['amount_cents']))


class AnalysisBoundary(unittest.TestCase):
    def setUp(self):
        with (ROOT/'data/transactions.csv').open() as f: self.rows=list(csv.DictReader(f))
        self.plan=dict(kind='analysis',dataset_id='personal_ledger',semantic_version=1,metric='net_expense',start='2026-09-01',end_exclusive='2026-10-01',filters={'category':'餐饮'},group_by=None,limit=100)

    def test_scope_and_external_user_not_accepted_in_plan(self):
        actual=query(self.plan,'test_user')
        other=query(self.plan,'isolation_user')
        self.assertNotEqual(actual['value_cents'],other['value_cents'])
        self.assertFalse(set(actual['evidence_ids']) & set(other['evidence_ids']))
        self.plan['user_id']='isolation_user'
        with self.assertRaisesRegex(ValueError,'INVALID_SCHEMA'):query(self.plan,'test_user')

    def test_unknown_enum_injection_and_missing_scope(self):
        for cat in ('不存在的类别',"餐饮' OR 1=1 --"):
            self.plan['filters']['category']=cat
            with self.assertRaisesRegex(ValueError,'UNKNOWN_ENUM'):query(self.plan,'test_user')
        self.plan['filters']['category']='餐饮'
        with self.assertRaises(PermissionError):query(self.plan,'unknown')

    def test_missing_and_invalid_dates(self):
        self.plan['start']='2026-10-01'
        with self.assertRaisesRegex(ValueError,'INVALID_RANGE'):query(self.plan,'test_user')
        self.plan.pop('metric')
        with self.assertRaisesRegex(ValueError,'INVALID_SCHEMA'):query(self.plan,'test_user')

    def test_posted_only(self):
        self.plan['filters']['category']='购物'
        result=query(self.plan,'test_user')
        by_id={r['id']:r for r in self.rows}
        self.assertTrue(all(by_id[x]['status']=='POSTED' for x in result['evidence_ids']))

    def test_empty_result_and_local_month_boundary(self):
        self.plan.update(start='2030-01-01',end_exclusive='2030-02-01')
        self.assertEqual(query(self.plan,'test_user'),dict(value_cents=0,evidence_ids=[]))
        self.plan.update(start='2026-09-01',end_exclusive='2026-10-01')
        self.plan['filters']['category']='交通'
        result=query(self.plan,'test_user')
        by_id={r['id']:r for r in self.rows}
        boundary=[by_id[x] for x in result['evidence_ids'] if by_id[x]['merchant']=='时区边界']
        self.assertEqual([int(r['amount_cents']) for r in boundary],[101])


class CashflowAnchors(unittest.TestCase):
    def test_baseline_hand_calculation(self):
        r=simulate(12,13)
        self.assertEqual(r['points_cents'][10],452200)
        self.assertEqual(r['points_cents'][12],636200)
        self.assertEqual(r['points_cents'][29],500200)
        self.assertEqual((r['min_balance_cents'],r['min_day'],r['feasible']),(452200,11,True))

    def test_delay_and_replan(self):
        r=simulate(17,13)
        self.assertEqual((r['min_balance_cents'],r['min_day'],r['feasible']),(-187800,16,False))
        r=simulate(17,19)
        self.assertEqual((r['min_balance_cents'],r['min_day'],r['feasible']),(412200,16,True))

    def test_invalid_simulation_parameters(self):
        for salary,purchase in [(0,13),(31,13),(12,True)]:
            with self.assertRaises(ValueError):simulate(salary,purchase)
        with self.assertRaises(ValueError):simulate(daily_cents=-1)


if __name__=='__main__':unittest.main()
