"""Local-only C4 gates. Structured evidence records exact source/SHA and exits.

No real service credentials, real LLM or payment API are used by these gates.
External acceptance remains BLOCKED, independently of this runner's exit code.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('/tmp/futureledger-c4-evidence'))
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'FUTURELEDGER_PROVIDER':'local','COVERAGE_FILE':str(out/'.coverage')}
    def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    report={'started_at_utc':datetime.now(timezone.utc).isoformat(),'branch':git('branch','--show-current'),
            'commit':git('rev-parse','HEAD'),'dirty':bool(git('status','--porcelain')),
            'python':sys.version.split()[0],'commands':[],'source_sha256':{}}
    for folder in ('services','tests','scripts','contracts'):
        for path in sorted((ROOT/folder).rglob('*')):
            if path.is_file() and path.suffix in {'.py','.json'}:
                report['source_sha256'][str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('requirements-c4.txt','requirements-demo.txt','.github/workflows/c-module.yml'):
        p=ROOT/name
        if p.is_file():report['source_sha256'][name]=hashlib.sha256(p.read_bytes()).hexdigest()
    py=sys.executable
    gates=[('fixture',[py,'scripts/prepare_c4_fixture.py']),
        ('dependencies',[py,'-m','pip','check']),
        ('static',[py,'-m','ruff','check','--select','F821,F822,F823','services','tests','scripts','demo']),
        ('api_export',[py,'-m','services.api.export_contracts','--check']),
        ('unittest',[py,'-m','coverage','run','--branch','--source=services/api','scripts/c4_unittest.py','--output',str(out/'unittest.json')]),
        ('coverage',[py,'-m','coverage','json','-o',str(out/'coverage.json')]),
        ('agent_benchmark',[py,'scripts/run_c4_benchmark.py','--output',str(out/'agent-benchmark.json')]),
        ('reference_oracle',[py,'scripts/run_benchmark.py']),
        ('reference_self_checks',[py,'-m','unittest','discover','-s','tests','-p','test_reference.py','-v']),
        ('prototype',['node','scripts/check_prototype.cjs']),
        ('process_restart',[py,'scripts/check_c4_restart.py']),
        ('diff',['git','diff','--check'])]
    for name,command in gates:
        start=time.monotonic();log=out/(name+'.log')
        with log.open('w') as stream:
            result=subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        item={'name':name,'command':command,'exit_code':result.returncode,
              'seconds':round(time.monotonic()-start,3),'log_file':log.name,
              'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest()}
        report['commands'].append(item)
        print(name+': '+str(result.returncode),flush=True)
    if (out/'unittest.json').is_file():report['test_matrix']=json.loads((out/'unittest.json').read_text())['groups']
    if (out/'coverage.json').is_file():
        report['coverage_totals']=json.loads((out/'coverage.json').read_text())['totals']
    oracle=ROOT/'benchmark/results.local.json'
    if oracle.is_file():
        ref=json.loads(oracle.read_text());report['reference_baseline']={k:v for k,v in ref.items() if k!='results'}
    report['completed_at_utc']=datetime.now(timezone.utc).isoformat()
    report['gates_passed']=all(c['exit_code']==0 for c in report['commands'])
    report['external_integration']='BLOCKED: formal A/B/D services unavailable'
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    raise SystemExit(not report['gates_passed'])
if __name__=='__main__':main()
