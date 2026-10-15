"""Offline oracle agreement only; no LLM or bank execution is measured."""
import argparse
import json
from pathlib import Path
from reference_engine import query,simulate

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--predictions',type=Path,help='JSON array of {id,value_cents,evidence_ids}; absent IDs fail')
    args=parser.parse_args()
    analysis=json.loads((ROOT/'benchmark/analysis_cases.json').read_text())
    cash=json.loads((ROOT/'benchmark/cashflow_cases.json').read_text())
    predictions=None
    if args.predictions:
        supplied=json.loads(args.predictions.read_text())
        predictions={x['id']:{k:v for k,v in x.items() if k!='id'} for x in supplied}
        if len(predictions)!=len(supplied): raise ValueError('duplicate prediction IDs')
    results=[]
    for case in analysis:
        try:
            actual=predictions.get(case['id']) if predictions is not None else query(case['plan'],case['user_id'])
            ok=actual==case['expected']
            results.append(dict(id=case['id'],split=case['split'],passed=ok))
        except Exception as e: results.append(dict(id=case['id'],passed=False,error=str(e)))
    if predictions is None:
        for case in cash:
            actual=simulate(case['salary_day'],case['purchase_day'])
            results.append(dict(id=case['id'],passed=actual==case['expected']))
    report=dict(mode='submitted_analysis_predictions' if predictions is not None else 'offline_reference_oracle_agreement',total=len(results),passed=sum(r['passed'] for r in results),failed=sum(not r['passed'] for r in results),not_measured=['LLM intent accuracy','bank execution','authorization','concurrency','sandbox isolation','worker recovery'],results=results)
    (ROOT/'benchmark/results.local.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    raise SystemExit(1 if report['failed'] else 0)


if __name__=='__main__':main()
