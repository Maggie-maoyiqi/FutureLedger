"""Read-only fixture adapters; explicit unavailable ports for A/B/D integration."""

import json
from contextlib import closing
from pathlib import Path
from typing import Protocol

from demo.query import Ledger, QueryError, audit_sql

from .auth import Principal
from .errors import AgentError, forbidden, unsupported
from .schemas import AnalysisPlan, AnalysisResult


class Directory(Protocol):
    def accounts(self, principal: Principal) -> tuple[str, ...]: ...
    def recipients(self, principal: Principal) -> list[dict]: ...
    def mandates(self, principal: Principal) -> list[dict]: ...
    def protected(self, principal: Principal) -> set[str]: ...


class FixtureDirectory:
    """Synthetic lookup metadata, not a live bank adapter or mandate read grant."""
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def _read(self, name):
        return json.loads((self.data_dir / (name + '.json')).read_text(encoding='utf-8'))

    def accounts(self, principal):
        return tuple(row['id'] for row in self._read('accounts')
                     if row['user_id'] == principal.subject and row['id'] in principal.account_ids)

    def recipients(self, principal):
        return [row for row in self._read('recipients') if row['user_id'] == principal.subject]

    def mandates(self, principal):
        # Internal resolver metadata only. Never expose a bank mandate list to chat.
        accounts = self.accounts(principal)
        return [row for row in self._read('mandates')
                if row['user_id'] == principal.subject and row['account_id'] in accounts]

    def protected(self, principal):
        profile = self._read('test_user')
        visible = {m['id'] for m in self.mandates(principal)}
        return set(profile.get('protected_mandate_ids', ())) & visible if profile['user_id'] == principal.subject else set()


class AnalysisGateway(Protocol):
    def analyze(self, plan: AnalysisPlan, principal: Principal) -> AnalysisResult: ...


class UnavailableAnalysis:
    def analyze(self, plan, principal):
        raise unsupported('D 分析服务')


class FixtureAnalysis:
    """Reuse read-only connections/auditing, retaining static metric semantics."""
    def __init__(self, path: Path):
        self.ledger = Ledger(path)

    def analyze(self, plan: AnalysisPlan, principal: Principal) -> AnalysisResult:
        try:
            accounts = tuple(a for a in self.ledger.accounts(principal.subject) if a in principal.account_ids)
            if not accounts:
                raise forbidden()
            with closing(self.ledger.connect()) as conn:
                # Do not silently import Demo's transfer-inclusive metric semantics.
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='demo_transfers'").fetchone():
                    raise unsupported('Demo runtime 数据不符合 C1 静态分析口径')
                kinds = ('expense',) if plan.metric == 'gross_expense' else ('refund',) if plan.metric == 'refund' else ('expense', 'refund')
                marks = ','.join('?' for _ in accounts)
                where = (f"user_id=? AND account_id IN ({marks}) AND status='POSTED' "
                         "AND date(posted_at,'+8 hours')>=? AND date(posted_at,'+8 hours')<? AND category=? "
                         f"AND kind IN ({','.join('?' for _ in kinds)})")
                params = (principal.subject, *accounts, plan.start, plan.end_exclusive, plan.filters.category, *kinds)
                # These expressions implement the original static contract, excluding transfers.
                value = ("CASE WHEN kind='expense' AND direction='OUT' THEN amount_cents "
                         "WHEN kind='refund' AND direction='IN' THEN -amount_cents ELSE 0 END"
                         if plan.metric == 'net_expense' else
                         "CASE WHEN direction='OUT' THEN amount_cents ELSE 0 END"
                         if plan.metric == 'gross_expense' else
                         "CASE WHEN direction='IN' THEN amount_cents ELSE 0 END")
                sql = f"SELECT COALESCE(SUM({value}),0) AS value_cents FROM transactions WHERE {where}"
                audit_sql(sql)
                rows = [dict(row) for row in conn.execute(sql, params)]
                evidence_sql = f"SELECT id FROM transactions WHERE {where} ORDER BY id LIMIT 5001"
                audit_sql(evidence_sql)
                evidence = [r[0] for r in conn.execute(evidence_sql, params)]
                if len(evidence) > 5000:
                    raise AgentError('TOO_MANY_EVIDENCE_ROWS', 422)
            return AnalysisResult(rows=rows, evidence_ids=evidence)
        except QueryError as exc:
            raise AgentError(exc.code, 503 if exc.code == 'FIXTURE_MISSING' else 422) from exc
