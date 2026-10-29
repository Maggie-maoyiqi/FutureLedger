"""Local Agent pipeline benchmark and explicitly oracle-backed MOCK wiring probes.

Gold answers are consulted only after Agent execution. Cashflow fixtures are
returned by test D, so cashflow scores measure wiring, NOT numerical correctness.
"""
import json
from datetime import date
from pathlib import Path
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.agent.adapters import FixtureAnalysis
from services.api.agent.auth import DevelopmentTokens
from services.api.agent.planner import LocalPlanner
from test_dag_c2 import P
from c3_fakes import FakeAnalytics, FakeBankRead

ROOT=Path(__file__).resolve().parents[1]

class BlindPlanner(LocalPlanner):
    def __init__(self): self.calls=0
    async def plan(self,text,today,context):
        # Assert the actual structured input has no oracle answer/plan fields.
        def inspect(value):
            if isinstance(value,dict):
                assert not set(value)&{'expected','evidence_ids','value_cents','points_cents','gold_plan'}
                for item in value.values():inspect(item)
            elif isinstance(value,list):
                for item in value:inspect(item)
        inspect(context)
        self.calls+=1
        return await super().plan(text,today,context)

class OracleWireD(FakeAnalytics):
    provenance='mock_D'
    def __init__(self,cases):
        super().__init__()
        self.fixtures={(x['salary_day'],x['purchase_day']):x['expected'] for x in cases}
    async def simulate_cashflow(self,p,inputs,constraints):
        self.calls.append(inputs.model_dump())
        salary=int((inputs.salary_date or '2026-11-12')[-2:])
        expected=self.fixtures[(salary,int(inputs.target_date[-2:]))]
        return dict(points=[dict(day=f'2026-11-{i:02}',balance_cents=v)
                           for i,v in enumerate(expected['points_cents'],1)],
            min_balance_cents=expected['min_balance_cents'],feasible=expected['feasible'],
            conflicts=[] if expected['feasible'] else ['MOCK: below protected floor'],
            assumptions=dict(source_account_id=inputs.source_account_id,target_date=inputs.target_date,
                             target_amount_cents=inputs.amount_cents,salary_date=inputs.salary_date),
            constraints=constraints.model_dump())

def run():
    planner=BlindPlanner()
    auth=DevelopmentTokens({'c4_local_benchmark_only':P})
    headers={'Authorization':'Bearer c4_local_benchmark_only'}
    app=create_app(authenticator=auth,planner=planner,analysis=FixtureAnalysis(ROOT/'data/benchmark.sqlite'),
                   clock=lambda:date(2026,10,1))
    analysis=[]
    with TestClient(app) as client:
        for i,case in enumerate(json.loads((ROOT/'benchmark/analysis_cases.json').read_text())):
            if i%60==0:sid=client.post('/api/sessions',json={},headers=headers).json()['data']['session_id']
            response=client.post('/api/agent/chat',headers=headers,json={'session_id':sid,'text':case['question']})
            report=response.json().get('data') or {}
            output=next(iter(report.get('outputs',{}).values()),{})
            actual={'value_cents':output.get('rows',[{}])[0].get('value_cents'),
                    'evidence_ids':output.get('evidence_ids')}
            analysis.append({'id':case['id'],'split':case['split'],
                             'status':'PASS' if response.status_code==200 and actual==case['expected'] else 'FAIL'})
    cashcases=json.loads((ROOT/'benchmark/cashflow_cases.json').read_text())
    adapter=OracleWireD(cashcases)
    app=create_app(authenticator=auth,planner=planner,bank_read_adapter=FakeBankRead(),analytics_adapter=adapter,
                   clock=lambda:date(2026,10,1))
    cash=[]
    with TestClient(app) as client:
        for case in cashcases:
            sid=client.post('/api/sessions',json={},headers=headers).json()['data']['session_id']
            # No gold values/points are planner inputs; the test adapter holds oracle fixtures.
            text=f"买电脑，目标存6000元，2026-11-{case['purchase_day']:02}，来源acc_daily，至少保留3000元"
            report=client.post('/api/agent/chat',headers=headers,json={'session_id':sid,'text':text}).json()['data']
            response=client.post('/api/agent/plans/'+report['plan_id']+'/replan',headers=headers,
                json={'plan_version':1,'reason':'user_changed','updates':{'salary_date':f"2026-11-{case['salary_day']:02}"}})
            data=response.json().get('data') or {}
            output=next((v for v in data.get('outputs',{}).values() if 'points' in v),{})
            ok=response.status_code==200 and data.get('plan_version')==2
            ok=ok and [p['balance_cents'] for p in output.get('points',[])]==case['expected']['points_cents']
            ok=ok and output.get('feasible')==case['expected']['feasible']
            cash.append({'id':case['id'],'status':'PASS' if ok else 'FAIL'})
    def report(mode,rows):
        return {'mode':mode,'PASS':sum(x['status']=='PASS' for x in rows),
                'FAIL':sum(x['status']=='FAIL' for x in rows),'results':rows}
    specs=json.loads((ROOT/'benchmark/acceptance_cases.json').read_text())
    acceptance=[{'id':c['id'],'source_status':c['status'],
                 'status':'NOT_RUN' if c['group']=='sandbox' else 'BLOCKED',
                 'reason':'Full sandbox suite belongs to E' if c['group']=='sandbox' else
                          'Full specification requires absent formal bank/gate/analytics and durable audit evidence'} for c in specs]
    return {'analysis':report('LOCAL_AGENT_C1_FIXTURE_NO_LLM_NO_FORMAL_D',analysis),
            'cashflow':report('MOCK_ORACLE_WIRE_ONLY_NOT_D_NUMERICAL_VALIDATION',cash),
            'planner_calls':planner.calls,'acceptance':acceptance,
            'real_service_integration':{'A':'BLOCKED','B':'BLOCKED','D':'BLOCKED','real_llm':'NOT_RUN'}}
