"""Two distinct OS processes prove state LOSS, not recovery or durable SSE."""
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def worker():
    from datetime import date
    from fastapi.testclient import TestClient
    from services.api.app import create_app
    from services.api.agent.auth import DevelopmentTokens,Principal
    from services.api.agent.adapters import FixtureAnalysis
    auth=DevelopmentTokens({'synthetic_restart_token':Principal('test_user',('acc_daily','acc_savings'),('personal_ledger',))})
    app=create_app(authenticator=auth,analysis=FixtureAnalysis(ROOT/'data/benchmark.sqlite'),clock=lambda:date(2026,10,1))
    payload=json.loads(sys.stdin.read())
    with TestClient(app) as client:
        headers={'Authorization':'Bearer synthetic_restart_token'}
        if not payload:
            sid=client.post('/api/sessions',headers=headers,json={}).json()['data']['session_id']
            response=client.post('/api/agent/chat',headers=headers,json={'session_id':sid,'text':'2026-09的餐饮支出是多少？'})
            report=response.json()['data']
            assert report['outputs']['analysis']['rows'][0]['value_cents']==221842
            print(json.dumps({'session_id':sid,'plan_id':report['plan_id']}))
        else:
            session=client.post('/api/chat',headers=headers,json={'session_id':payload['session_id'],'text':'上次那个'})
            plan=client.get('/api/agent/plans/'+payload['plan_id']+'/report',headers=headers)
            events=client.get('/api/events',headers={**headers,'Last-Event-ID':'999999'})
            assert (session.status_code,plan.status_code,events.status_code)==(403,403,409)
            print(json.dumps({'fresh_process_old_session':403,'fresh_process_old_plan':403,
                'unavailable_event_cursor':409,'durable_recovery':'NOT_IMPLEMENTED',
                'state_loss_fail_closed':'PASS','processes':2}))

def main():
    def call(payload):
        result=subprocess.run([sys.executable,__file__,'--worker'],input=json.dumps(payload),text=True,
                              capture_output=True,cwd=ROOT,check=True,timeout=40)
        return json.loads(result.stdout)
    previous=call({})
    print(json.dumps(call(previous)))

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:main()
