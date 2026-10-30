"""C-owned boundary defects: response scope, truth consistency and timezone."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from services.api.agent.adapters import FixtureDirectory, FixtureAnalysis
from services.api.agent.schemas import AnalysisPlan, Constraints
from services.api.agent.orchestration.registry import build_registry
from services.api.agent.orchestration.schemas import CallContext
from services.api.agent.errors import AgentError
from c3_fakes import FakeBankRead, FakeGate, FakeAnalytics
from test_dag_c2 import P


ROOT=Path(__file__).resolve().parents[1]


class FixtureTimezoneTests(unittest.TestCase):
    def test_mutated_typed_b_response_is_revalidated(self):
        from services.api.agent.legacy import LegacyBoundary
        from services.api.agent.ports import OperationState
        state=OperationState(operation_id='operation',plan_id='plan',plan_version=1,status='READY',next_action='')
        import warnings
        state.status='sk-C4SYNTHETICTOKEN123456789'
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter('always')
            with self.assertRaises(AgentError) as error:
                LegacyBoundary(None,None)._parse(state,plan_id='plan',version=1)
        self.assertEqual(recorded,[])
        self.assertEqual(error.exception.code,'INVALID_UPSTREAM_RESULT')

    def test_utc_instants_are_filtered_by_shanghai_business_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE accounts(id TEXT,user_id TEXT)')
                db.execute("INSERT INTO accounts VALUES('acc_daily','test_user')")
                db.execute('CREATE TABLE transactions(id TEXT,user_id TEXT,account_id TEXT,status TEXT,posted_at TEXT,category TEXT,kind TEXT,direction TEXT,amount_cents INTEGER)')
                db.executemany('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?)',[
                    ('included','test_user','acc_daily','POSTED','2026-08-31T16:00:00+00:00','餐饮','expense','OUT',123),
                    ('excluded','test_user','acc_daily','POSTED','2026-09-30T16:00:00+00:00','餐饮','expense','OUT',999)])
            plan=AnalysisPlan.model_validate(dict(kind='analysis',dataset_id='personal_ledger',semantic_version=1,
                metric='gross_expense',start='2026-09-01',end_exclusive='2026-10-01',filters={'category':'餐饮'},group_by=None,limit=100))
            result=FixtureAnalysis(path).analyze(plan,P)
            self.assertEqual(result.rows[0].value_cents,123)
            self.assertEqual(result.evidence_ids,['included'])


class AdapterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.a,self.b,self.d=FakeBankRead(),FakeGate(),FakeAnalytics()
        self.r=build_registry(FixtureDirectory(ROOT/'data'),self.a,self.b,self.d)
        self.ctx=CallContext(P,Constraints(min_balance_cents=300000),'plan',1,'node')

    async def test_typed_tool_response_cannot_bypass_strict_types(self):
        from services.api.agent.dag.schemas import BalanceResult
        result=BalanceResult(available_cents=900000)
        result.available_cents='900000'
        async def malformed(*args): return result
        self.a.get_balance=malformed
        with self.assertRaises(AgentError) as error:
            await self.r.invoke('get_balance',{'source_account_id':'acc_daily'},self.ctx)
        self.assertEqual(error.exception.code,'INVALID_TOOL_RESULT')

    async def test_transaction_dates_and_requested_limit_are_enforced(self):
        read=self.a.list_transactions
        async def outside(p,inputs):
            result=await read(p,inputs)
            result['transactions'][0]['posted_on']='2026-08-31'
            return result
        self.a.list_transactions=outside
        with self.assertRaises(AgentError) as error:
            await self.r.invoke('list_transactions',{'source_account_id':'acc_daily','start':'2026-09-01',
                'end_exclusive':'2026-10-01','limit':1},self.ctx)
        self.assertEqual(error.exception.code,'INVALID_TOOL_RESULT')
        async def too_many(p,inputs):
            result=await read(p,inputs)
            result['transactions']*=2
            return result
        self.a.list_transactions=too_many
        with self.assertRaises(AgentError):
            await self.r.invoke('list_transactions',{'source_account_id':'acc_daily','start':'2026-09-01',
                'end_exclusive':'2026-10-01','limit':1},self.ctx)

    async def test_cashflow_feasibility_cannot_contradict_explicit_constraints(self):
        simulate=self.d.simulate_cashflow
        async def contradictory(p,inputs,constraints):
            result=await simulate(p,inputs,constraints)
            result['min_balance_cents']=-1
            result['feasible']=True
            return result
        self.d.simulate_cashflow=contradictory
        with self.assertRaises(AgentError) as error:
            await self.r.invoke('simulate_cashflow',{'source_account_id':'acc_daily','amount_cents':600000,
                'target_date':'2026-12-01'},self.ctx)
        self.assertEqual(error.exception.code,'INVALID_TOOL_RESULT')

    async def test_all_seven_a_read_contracts_and_yellow_confirmation(self):
        from dataclasses import replace
        ctx=replace(self.ctx,scope_ref='mock_scope')
        self.b.scope_approved=True
        requests=[('get_balance',{'source_account_id':'acc_daily'}),
            ('list_transactions',{'source_account_id':'acc_daily','start':'2026-09-01','end_exclusive':'2026-10-01'}),
            ('resolve_recipient',{'source_account_id':'acc_daily','recipient_id':'rec_1'}),
            ('quote_transfer',{'source_account_id':'acc_daily','recipient_id':'rec_1','amount_cents':100,'execution_date':'2026-11-02'}),
            ('get_transfer_status',{'source_account_id':'acc_daily','reference':'mock_transfer'}),
            ('list_mandates',{'source_account_id':'acc_daily'}),
            ('get_mandate_status',{'source_account_id':'acc_daily','mandate_id':'mandate_2'})]
        for name,inputs in requests:
            with self.subTest(tool=name):
                result=await self.r.invoke(name,inputs,ctx)
                self.r.get(name).output_schema.model_validate(result)
