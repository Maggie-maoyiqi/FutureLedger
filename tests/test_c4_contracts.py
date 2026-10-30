"""Export and validate C API contracts without changing A/B/D truth."""
import json
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator
from c4_support import Harness
from test_agent_c1 import intent

ROOT=Path(__file__).resolve().parents[1]

class ApiContracts(unittest.TestCase):
    def setUp(self):
        self.h=Harness();self.addCleanup(self.h.close)
        self.schema=self.h.app.openapi()
    def validate(self,response,path,status):
        self.assertEqual(response.status_code,status,response.text)
        schema=self.schema['paths'][path]['post']['responses'][str(status)]['content']['application/json']['schema']
        Draft202012Validator({**schema,'components':self.schema['components']}).validate(response.json())
        data=response.json()
        self.assertEqual(set(data),{'data','error','request_id'})
        self.assertTrue(data['request_id'])
        if data['error']:
            self.assertEqual(set(data['error']),{'code','recoverable','next_action'})
    def test_runtime_success_error_envelopes_validate_against_openapi(self):
        self.validate(self.h.transfer(),'/api/agent/chat',200)
        self.validate(self.h.request('/api/agent/chat',{'session_id':self.h.sid,'text':'x','user_id':'forged'}),'/api/agent/chat',422)
        self.validate(self.h.request('/api/agent/chat',{'session_id':self.h.sid,'text':'x'},headers={}),'/api/agent/chat',401)
    def test_all_schema_files_are_valid_json_schema(self):
        paths=list((ROOT/'contracts').glob('*.schema.json'))
        self.assertGreaterEqual(len(paths),4)
        for path in paths:
            with self.subTest(file=path.name):Draft202012Validator.check_schema(json.loads(path.read_text()))
    def test_four_intents_missing_inputs_and_scope_rejection(self):
        for kind in ('analysis','transfer','mandate','goal'):
            with self.subTest(intent=kind):
                self.h.provider.result=intent(kind)
                response=self.h.request('/api/agent/chat',{'session_id':self.h.sid,'text':'补充信息'})
                self.assertEqual(response.status_code,200,response.text)
                report=response.json()['data']
                self.assertEqual(report['execution_status'],'NEEDS_INFO')
                self.assertEqual(report['clarification']['intent'],kind)
                # Analysis has no source-account field in the frozen contract:
                # exercise subject isolation instead of constructing an invalid intent.
                self.h.provider.result=intent(kind,**({} if kind=='analysis' else {'source_account_id':'acc_other'}))
                headers={'Authorization':'Bearer bob'} if kind=='analysis' else self.h.headers
                response=self.h.request('/api/agent/chat',{'session_id':self.h.sid,'text':'查询'},headers=headers)
                self.assertEqual(response.status_code,403,response.text)
    def test_unsupported_analysis_capabilities_are_not_silently_executed(self):
        for metric in ('income','merchant_ranking','monthly_trend'):
            self.h.provider.result=intent('analysis',analysis_plan=dict(kind='analysis',dataset_id='personal_ledger',
                semantic_version=1,metric=metric,start='2026-09-01',end_exclusive='2026-10-01',
                filters={'category':'餐饮'},group_by=None,limit=100))
            response=self.h.request('/api/agent/chat',{'session_id':self.h.sid,'text':'分析'})
            self.assertEqual(response.status_code,502,response.text)
        self.assertEqual(self.h.d.calls,[])
