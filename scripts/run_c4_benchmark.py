"""Run natural-language Agent benchmark; never feed gold plans to planner."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from c4_benchmark import run

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    report=run()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:{a:b for a,b in v.items() if a!='results'} for k,v in report.items() if k in {'analysis','cashflow'}},ensure_ascii=False))
    raise SystemExit(bool(report['analysis']['FAIL'] or report['cashflow']['FAIL']))
if __name__=='__main__':main()
