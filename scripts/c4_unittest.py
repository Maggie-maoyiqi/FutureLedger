"""Run discovered tests once; emit non-overlapping counts without payload traces."""
import argparse
import json
import sys
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]

GROUPS={'test_c4_unit':'C Unit Tests','test_c4_integration':'C Integration Tests','test_c4_contracts':'C Integration Tests',
        'test_c4_security':'Security Tests','test_c4_faults':'Fault Injection Tests'}

class Evidence(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.items={}
    def identifier(self,test): return unittest.TestCase.id(test)
    def mark(self,test,status):
        identifier=self.identifier(test)
        self.items[identifier]={'id':identifier,'status':status}
    def addSuccess(self,test):
        super().addSuccess(test)
        if self.identifier(test) not in self.items:self.mark(test,'PASS')
    def addFailure(self,test,err):super().addFailure(test,err);self.mark(test,'FAIL')
    def addError(self,test,err):super().addError(test,err);self.mark(test,'FAIL')
    def addSkip(self,test,reason):super().addSkip(test,reason);self.mark(test,'SKIP')
    def addSubTest(self,test,subtest,err):
        super().addSubTest(test,subtest,err)
        if err:self.mark(test,'FAIL')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    start=time.monotonic()
    result=unittest.TextTestRunner(verbosity=2,resultclass=Evidence).run(
        unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    groups={}
    for row in result.items.values():
        group=GROUPS.get(row['id'].split('.')[0],'Regression Tests')
        counts=groups.setdefault(group,{'PASS':0,'FAIL':0,'SKIP':0})
        counts[row['status']]+=1
    data={'total':result.testsRun,'duration_seconds':round(time.monotonic()-start,3),
          'groups':groups,'tests':list(result.items.values())}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    raise SystemExit(not result.wasSuccessful())

if __name__=='__main__':main()
