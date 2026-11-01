"""Deterministic read-only query execution and atomic synthetic transfers."""

import sqlite3
import secrets
from datetime import datetime
from contextlib import closing
from pathlib import Path

from .semantic import CATEGORIES, IntentPlan

AMOUNT = {
    "gross_expense": "COALESCE(SUM(CASE WHEN kind='expense' AND direction='OUT' THEN amount_cents ELSE 0 END),0)",
    "refund": "COALESCE(SUM(CASE WHEN kind='refund' AND direction='IN' THEN amount_cents ELSE 0 END),0)",
    "net_expense": "COALESCE(SUM(CASE WHEN kind='expense' AND direction='OUT' THEN amount_cents WHEN kind='refund' AND direction='IN' THEN -amount_cents ELSE 0 END),0)",
    "income": "COALESCE(SUM(CASE WHEN kind='income' AND direction='IN' THEN amount_cents ELSE 0 END),0)",
    "transaction_count": "COALESCE(SUM(CASE WHEN kind IN ('expense','refund') THEN 1 ELSE 0 END),0)",
    "avg_expense": "ROUND(SUM(CASE WHEN kind='expense' AND direction='OUT' THEN amount_cents ELSE 0 END)*1.0/NULLIF(SUM(CASE WHEN kind='expense' AND direction='OUT' THEN 1 ELSE 0 END),0),2)",
    "transfer_amount": "COALESCE(SUM(CASE WHEN kind='transfer' AND direction='OUT' THEN amount_cents ELSE 0 END),0)",
    "transfer_count": "COALESCE(SUM(CASE WHEN kind='transfer' AND direction='OUT' THEN 1 ELSE 0 END),0)",
}
GROUP = {"category": "category", "merchant": "merchant", "month": "substr(posted_at,1,7)", "account": "account_id"}
KINDS = {
    "gross_expense": ("expense",), "refund": ("refund",),
    "net_expense": ("expense", "refund"), "income": ("income",),
    "transaction_count": ("expense", "refund"), "avg_expense": ("expense",),
    "transfer_amount": ("transfer",), "transfer_count": ("transfer",),
}
# Detail views are ledger activity views. They keep transfer rows visible for
# the existing demo's auditability, while aggregate expense metrics exclude
# transfers from consumption calculations.
DETAIL_KINDS = {
    **KINDS,
    "gross_expense": ("expense", "transfer"),
    "net_expense": ("expense", "transfer", "refund"),
    "income": ("income", "transfer"),
    "transaction_count": ("expense", "refund", "transfer"),
    "avg_expense": ("expense", "transfer"),
}
FORBIDDEN = ("delete", "update", "insert", "drop", "alter", "truncate", "attach", "pragma", "--", "/*", ";")
ACCOUNT_NAMES = {"acc_daily": "主账户", "acc_savings": "储蓄账户", "acc_other": "演示账户"}


class QueryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def audit_sql(sql: str):
    lowered = sql.lower()
    if not lowered.lstrip().startswith("select ") or any(token in lowered for token in FORBIDDEN):
        raise QueryError("UNSAFE_SQL", "查询编译器生成了非只读语句")
    if " from transactions " not in lowered:
        raise QueryError("UNSAFE_SQL", "只允许访问已注册的流水事实表")


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def connect(self):
        if not self.path.is_file():
            raise QueryError("FIXTURE_MISSING", "缺少离线数据；请先运行 python3 scripts/generate_data.py")
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def accounts(self, user_id: str):
        with closing(self.connect()) as conn:
            return tuple(row[0] for row in conn.execute("SELECT id FROM accounts WHERE user_id=? ORDER BY id", (user_id,)))

    def account_snapshot(self, user_id: str):
        """Return current balances for the authenticated user's accounts only."""
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT id,balance_cents FROM accounts WHERE user_id=? ORDER BY id",
                (user_id,),
            )
            return [
                {"account_id": row["id"], "account_name": ACCOUNT_NAMES.get(row["id"], "银行账户"),
                 "balance_cents": row["balance_cents"]}
                for row in rows
            ]

    def merchants(self, user_id: str, account_ids: tuple[str, ...]):
        if not account_ids:
            return ()
        marks = ",".join("?" for _ in account_ids)
        with closing(self.connect()) as conn:
            return tuple(r[0] for r in conn.execute(
                f"SELECT DISTINCT merchant FROM transactions WHERE user_id=? AND account_id IN ({marks}) ORDER BY merchant",
                (user_id, *account_ids),
            ) if r[0])

    def llm_context(self, user_id: str, account_ids: tuple[str, ...], limit: int = 30):
        """Bounded, current-user-only transaction examples for intent resolution."""
        if not account_ids:
            return {"user_id": user_id, "account_ids": [], "recent_transactions": []}
        marks = ",".join("?" for _ in account_ids)
        with closing(self.connect()) as conn:
            rows = conn.execute(
                f"SELECT posted_at, category, merchant, kind, direction, amount_cents "
                f"FROM transactions WHERE user_id=? AND account_id IN ({marks}) AND status='POSTED' "
                "ORDER BY posted_at DESC, id DESC LIMIT ?",
                (user_id, *account_ids, limit),
            )
            return {"user_id": user_id, "account_ids": list(account_ids),
                    "recent_transactions": [dict(row) for row in rows]}

    def compile(self, plan: IntentPlan, user_id: str, account_ids: tuple[str, ...], merchants: tuple[str, ...]):
        if not account_ids:
            raise QueryError("FORBIDDEN", "当前用户没有可查询的账户")
        if plan.filters.category is not None and plan.filters.category not in CATEGORIES:
            raise QueryError("UNKNOWN_ENUM", "找不到这个消费类别；请从已发布类别中选择")
        if plan.filters.merchant is not None and plan.filters.merchant not in merchants:
            raise QueryError("UNKNOWN_ENTITY", "找不到这个商户；不会忽略商户条件")
        if plan.filters.account_id is not None and plan.filters.account_id not in account_ids:
            raise QueryError("FORBIDDEN", "这个账户不在当前用户的授权范围内")

        marks = ",".join("?" for _ in account_ids)
        where = f"user_id=? AND account_id IN ({marks}) AND status='POSTED' AND substr(posted_at,1,10)>=? AND substr(posted_at,1,10)<?"
        where_params: list[object] = [user_id, *account_ids, plan.start.isoformat(), plan.end_exclusive.isoformat()]
        if plan.filters.category is not None:
            where += " AND category=?"; where_params.append(plan.filters.category)
        if plan.filters.merchant is not None:
            where += " AND merchant=?"; where_params.append(plan.filters.merchant)
        if plan.filters.account_id is not None:
            where += " AND account_id=?"; where_params.append(plan.filters.account_id)

        if plan.kind == "detail":
            kinds = DETAIL_KINDS[plan.metric]
            kind_marks = ",".join("?" for _ in kinds)
            sql = ("SELECT posted_at, category, merchant, kind, direction, amount_cents "
                   f"FROM transactions WHERE {where} AND kind IN ({kind_marks}) "
                   f"ORDER BY posted_at {'DESC' if plan.sort=='date_desc' else 'ASC'}, id ASC LIMIT ?")
            params = [*where_params, *kinds, plan.limit]
        elif plan.group_by:
            dimension = GROUP[plan.group_by]
            series = f", {GROUP[plan.series_by]} AS series_key" if plan.series_by else ""
            grouped = f"{dimension}, {GROUP[plan.series_by]}" if plan.series_by else dimension
            order = ("group_key ASC" if plan.sort == "date_asc" else
                     "group_key DESC" if plan.sort == "date_desc" else
                     "value_cents ASC, group_key ASC" if plan.sort == "metric_asc" else
                     "value_cents DESC, group_key ASC")
            if plan.series_by:
                order = "group_key ASC, series_key ASC"
            sql = (f"SELECT {dimension} AS group_key{series}, {AMOUNT[plan.metric]} AS value_cents "
                   f"FROM transactions WHERE {where} AND kind IN ({','.join('?' for _ in KINDS[plan.metric])}) "
                   f"GROUP BY {grouped} ORDER BY {order} LIMIT ?")
            params = [*where_params, *KINDS[plan.metric], plan.limit]
        else:
            sql = f"SELECT {AMOUNT[plan.metric]} AS value_cents FROM transactions WHERE {where} AND kind IN ({','.join('?' for _ in KINDS[plan.metric])})"
            params = [*where_params, *KINDS[plan.metric]]
        audit_sql(sql)
        return sql, tuple(params), where, tuple(where_params)

    def execute(self, plan: IntentPlan, user_id: str):
        accounts = self.accounts(user_id)
        merchants = self.merchants(user_id, accounts)
        sql, params, where, where_params = self.compile(plan, user_id, accounts, merchants)
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA query_only=ON")
            rows = [dict(r) for r in conn.execute(sql, params)]
            if plan.kind == "detail":
                kinds = DETAIL_KINDS[plan.metric]
                evidence_sql = (f"SELECT id FROM transactions WHERE {where} "
                                f"AND kind IN ({','.join('?' for _ in kinds)}) "
                                f"ORDER BY posted_at {'DESC' if plan.sort=='date_desc' else 'ASC'}, id ASC LIMIT ?")
                audit_sql(evidence_sql)
                evidence = [r[0] for r in conn.execute(evidence_sql, (*where_params, *kinds, plan.limit))]
            else:
                marks = ",".join("?" for _ in KINDS[plan.metric])
                evidence_sql = f"SELECT id FROM transactions WHERE {where} AND kind IN ({marks}) ORDER BY id LIMIT 5001"
                audit_sql(evidence_sql)
                evidence = [r[0] for r in conn.execute(evidence_sql, (*where_params, *KINDS[plan.metric]))]
        if len(evidence) > 5000:
            raise QueryError("TOO_MANY_EVIDENCE_ROWS", "凭据过多，请缩小日期范围")
        return {"rows": rows, "evidence_ids": evidence, "sql": sql}

    def execute_demo_transfer(self, action_id: str, sender_user: str, recipient_user: str, amount_cents: int,
                              category: str, posted_at: str | None = None):
        """Atomically update two synthetic accounts and append their paired ledger entries."""
        if sender_user == recipient_user:
            raise QueryError("INVALID_RECIPIENT", "不能向当前演示用户本人转账")
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT status FROM demo_transfers WHERE action_id=?", (action_id,)).fetchone()
            if prior:
                conn.commit()
                return {"status": prior["status"]}
            sender = conn.execute("SELECT id,balance_cents FROM accounts WHERE user_id=? ORDER BY id LIMIT 1", (sender_user,)).fetchone()
            recipient = conn.execute("SELECT id,balance_cents FROM accounts WHERE user_id=? ORDER BY id LIMIT 1", (recipient_user,)).fetchone()
            if not sender or not recipient:
                raise QueryError("DEMO_ACCOUNT_MISSING", "找不到演示账户")
            if sender["balance_cents"] < amount_cents:
                raise QueryError("INSUFFICIENT_FUNDS", "演示账户余额不足，未执行转账")
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            posted_at = posted_at or now
            transfer_ref = "demo_" + secrets.token_hex(12)
            conn.execute("UPDATE accounts SET balance_cents=balance_cents-? WHERE id=?", (amount_cents, sender["id"]))
            conn.execute("UPDATE accounts SET balance_cents=balance_cents+? WHERE id=?", (amount_cents, recipient["id"]))
            def add_transfer_row(user_id, account_id, direction, side):
                conn.execute(
                    "INSERT INTO transactions(id,external_id,user_id,account_id,amount_cents,direction,kind,status,category,merchant,posted_at,refund_of,memo) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("demo_"+secrets.token_hex(12), transfer_ref+"_"+side, user_id, account_id,
                     amount_cents, direction, "transfer", "POSTED", category if direction == "OUT" else "转账", "演示用户", posted_at, "",
                     "演示转账；双方账本成对记录"),
                )
            add_transfer_row(sender_user, sender["id"], "OUT", "out")
            add_transfer_row(recipient_user, recipient["id"], "IN", "in")
            conn.execute("INSERT INTO demo_transfers(action_id,sender_user_id,recipient_user_id,amount_cents,status,created_at,completed_at) VALUES(?,?,?,?,?,?,?)",
                         (action_id, sender_user, recipient_user, amount_cents, "SUCCEEDED", now, now))
            conn.commit()
            return {"status": "SUCCEEDED"}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
