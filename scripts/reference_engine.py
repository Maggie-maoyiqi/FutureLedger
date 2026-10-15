"""Small offline contract reference. Not an authenticated production SQL service."""
import sqlite3
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
METRICS={
 'gross_expense':"CASE WHEN kind='expense' THEN amount_cents ELSE 0 END",
 'refund':"CASE WHEN kind='refund' THEN amount_cents ELSE 0 END",
 'net_expense':"CASE WHEN kind='expense' THEN amount_cents WHEN kind='refund' THEN -amount_cents ELSE 0 END"
}
CATEGORIES={'餐饮','交通','购物','娱乐','订阅','房租','医疗','其他','工资','转账'}


def query(plan, authenticated_user, db=None):
    allowed={'kind','dataset_id','semantic_version','metric','start','end_exclusive','filters','group_by','limit'}
    if not isinstance(plan,dict) or set(plan)!=allowed: raise ValueError('INVALID_SCHEMA')
    if plan['kind']!='analysis' or plan['dataset_id']!='personal_ledger' or type(plan['semantic_version']) is not int or plan['semantic_version']!=1: raise ValueError('UNKNOWN_DATASET')
    if plan['metric'] not in METRICS or plan['group_by'] is not None: raise ValueError('UNSUPPORTED_PLAN')
    if type(plan['limit']) is not int or not 1<=plan['limit']<=100: raise ValueError('INVALID_LIMIT')
    filters=plan['filters']
    if not isinstance(filters,dict) or set(filters)!={'category'}: raise ValueError('INVALID_FILTER')
    if filters['category'] not in CATEGORIES: raise ValueError('UNKNOWN_ENUM')
    a,b=date.fromisoformat(plan['start']),date.fromisoformat(plan['end_exclusive'])
    if a>=b: raise ValueError('INVALID_RANGE')
    # Inputs are canonical local ISO dates in this fixture; production uses UTC boundaries.
    if a.isoformat()!=plan['start'] or b.isoformat()!=plan['end_exclusive']: raise ValueError('INVALID_DATE')
    if authenticated_user not in {'test_user','isolation_user'}: raise PermissionError('FORBIDDEN')
    path=Path(db) if db else ROOT/'data/benchmark.sqlite'
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    conn.execute('PRAGMA query_only=ON')
    where="user_id=? AND status='POSTED' AND kind IN ('expense','refund') AND substr(posted_at,1,10)>=? AND substr(posted_at,1,10)<? AND category=?"
    params=(authenticated_user,plan['start'],plan['end_exclusive'],filters['category'])
    try:
        amount=conn.execute('SELECT COALESCE(SUM('+METRICS[plan['metric']]+'),0) FROM transactions WHERE '+where,params).fetchone()[0]
        kind_filter={'gross_expense':" AND kind='expense'",'refund':" AND kind='refund'",'net_expense':''}[plan['metric']]
        ids=[r[0] for r in conn.execute('SELECT id FROM transactions WHERE '+where+kind_filter+' ORDER BY id',params)]
        return dict(value_cents=amount,evidence_ids=ids)
    finally: conn.close()


def simulate(salary_day=12,purchase_day=13,daily_cents=8000):
    if type(salary_day) is not int or type(purchase_day) is not int or not 1<=salary_day<=30 or not 1<=purchase_day<=30: raise ValueError('INVALID_DAY')
    if type(daily_cents) is not int or daily_cents<0: raise ValueError('INVALID_BUDGET')
    # Build event stream, distinct from generator's explicit recurrence oracle.
    events=[(5,-350000),(8,-3000),(10,-6800),(salary_day,800000),(purchase_day,-600000)]
    points=[];balance=900000
    for day in range(1,31):
        balance+=sum(value for when,value in events if when==day)-daily_cents
        points.append(balance)
    low=min(points)
    return dict(points_cents=points,min_balance_cents=low,min_day=points.index(low)+1,feasible=low>=300000)
