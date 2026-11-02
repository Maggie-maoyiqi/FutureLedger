"""Independent mixed-direction ledger contract, separate from formal C gold."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date
from demo.query import Ledger
from demo.semantic import IntentPlan


class ChecklistLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "ledger.sqlite"
        with sqlite3.connect(path) as conn:
            conn.executescript(
                "CREATE TABLE accounts(id TEXT,user_id TEXT); CREATE TABLE transactions(id TEXT,user_id TEXT,account_id TEXT,status TEXT,posted_at TEXT,category TEXT,merchant TEXT,kind TEXT,direction TEXT,amount_cents INTEGER);"
            )
            conn.execute("INSERT INTO accounts VALUES('a','u')")
            rows = [
                ("expense", "OUT", 1000),
                ("expense", "IN", 9000),
                ("income", "IN", 2000),
                ("income", "OUT", 9000),
                ("transfer", "OUT", 300),
                ("transfer", "OUT", 500),
                ("transfer", "IN", 700),
                ("refund", "IN", 100),
                ("refund", "OUT", 9000),
            ]
            for i, (kind, direction, amount) in enumerate(rows):
                conn.execute(
                    "INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(i),
                        "u",
                        "a",
                        "POSTED",
                        "2026-09-15",
                        "其他",
                        "商户",
                        kind,
                        direction,
                        amount,
                    ),
                )
        self.ledger = Ledger(path)

    def run_plan(self, metric, kind="aggregate"):
        return self.ledger.execute(
            IntentPlan(
                kind=kind,
                dataset_id="personal_ledger",
                semantic_version=1,
                metric=metric,
                start=date(2026, 9, 1),
                end_exclusive=date(2026, 10, 1),
            ),
            "u",
        )

    def test_inflow_outflow_totals_and_evidence(self):
        for metric, amount, ids in [
            ("gross_expense", 1800, ["0", "4", "5"]),
            ("net_expense", 1700, ["0", "4", "5", "7"]),
            ("income", 2700, ["2", "6"]),
            ("avg_expense", 600.0, ["0", "4", "5"]),
            ("transaction_count", 5, ["0", "4", "5", "6", "7"]),
        ]:
            with self.subTest(metric=metric):
                result = self.run_plan(metric)
                self.assertEqual(result["rows"], [{"value_cents": amount}])
                self.assertEqual(result["evidence_ids"], ids)

    def test_direction_correct_details_and_transfer_records(self):
        for metric, ids in [
            ("gross_expense", {"0", "4", "5"}),
            ("income", {"2", "6"}),
            ("net_expense", {"0", "4", "5", "7"}),
            ("transfer_amount", {"4", "5", "6"}),
        ]:
            with self.subTest(metric=metric):
                result = self.run_plan(metric, "detail")
                self.assertEqual(set(result["evidence_ids"]), ids)
                self.assertEqual(len(result["rows"]), len(ids))

    def test_transfer_metrics(self):
        for metric, amount, ids in [
            ("transfer_amount", 800, ["4", "5"]),
            ("transfer_income", 700, ["6"]),
            ("avg_transfer", 400.0, ["4", "5"]),
        ]:
            with self.subTest(metric=metric):
                result = self.run_plan(metric)
                self.assertEqual(result["rows"], [{"value_cents": amount}])
                self.assertEqual(result["evidence_ids"], ids)

    def test_grouped_outflow_and_empty_transfer_average(self):
        base = self.run_plan("gross_expense")
        plan = IntentPlan(
            kind="aggregate",
            dataset_id="personal_ledger",
            semantic_version=1,
            metric="gross_expense",
            start=date(2026, 9, 1),
            end_exclusive=date(2026, 10, 1),
            group_by="category",
        )
        result = self.ledger.execute(plan, "u")
        self.assertEqual(result["rows"], [{"group_key": "其他", "value_cents": 1800}])
        self.assertEqual(result["evidence_ids"], base["evidence_ids"])
        empty = plan.model_copy(
            update={
                "metric": "avg_transfer",
                "start": date(2027, 9, 1),
                "end_exclusive": date(2027, 10, 1),
                "group_by": None,
            }
        )
        result = self.ledger.execute(empty, "u")
        self.assertEqual(result["rows"], [{"value_cents": None}])
        self.assertEqual(result["evidence_ids"], [])
