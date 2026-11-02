import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date
from demo.query import Ledger
from demo.semantic import IntentPlan
from demo.explain import compare_period


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "ledger.db"
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE accounts(id TEXT,user_id TEXT); CREATE TABLE transactions(id TEXT,user_id TEXT,account_id TEXT,status TEXT,posted_at TEXT,category TEXT,merchant TEXT,kind TEXT,direction TEXT,amount_cents INTEGER);"
            )
            db.executemany(
                "INSERT INTO accounts VALUES(?,?)", [("a", "u"), ("secret", "other")]
            )
            rows = [
                (
                    "1",
                    "u",
                    "a",
                    "POSTED",
                    "2026-08-10",
                    "餐饮",
                    "店",
                    "expense",
                    "OUT",
                    1000,
                ),
                (
                    "2",
                    "u",
                    "a",
                    "POSTED",
                    "2026-09-10",
                    "餐饮",
                    "店",
                    "expense",
                    "OUT",
                    4000,
                ),
                (
                    "3",
                    "u",
                    "a",
                    "POSTED",
                    "2026-08-12",
                    "购物",
                    "店",
                    "expense",
                    "OUT",
                    3000,
                ),
                (
                    "4",
                    "u",
                    "a",
                    "POSTED",
                    "2026-09-12",
                    "购物",
                    "店",
                    "expense",
                    "OUT",
                    1000,
                ),
                (
                    "5",
                    "other",
                    "secret",
                    "POSTED",
                    "2026-09-12",
                    "购物",
                    "店",
                    "expense",
                    "OUT",
                    999999,
                ),
            ]
            db.executemany("INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        self.ledger = Ledger(path)
        self.plan = IntentPlan(
            kind="aggregate",
            dataset_id="personal_ledger",
            semantic_version=1,
            metric="gross_expense",
            start=date(2026, 9, 1),
            end_exclusive=date(2026, 10, 1),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_actual_changes_and_scope(self):
        p = compare_period(self.plan, self.ledger, "u")
        self.assertEqual(
            p["rows"],
            [
                {"group_key": "餐饮", "value_cents": 3000},
                {"group_key": "购物", "value_cents": -2000},
            ],
        )
        self.assertIn("增加", p["answer"])
        self.assertIn("10.00", p["answer"])
        self.assertNotIn("9999", p["answer"])
        self.assertIn("不能", p["answer"])

    def test_same_filter_and_decrease(self):
        plan = self.plan.model_copy(
            update={
                "filters": self.plan.filters.model_copy(update={"category": "购物"})
            }
        )
        p = compare_period(plan, self.ledger, "u")
        self.assertEqual(p["rows"], [{"group_key": "购物", "value_cents": -2000}])
        self.assertIn("减少", p["answer"])

    def test_ineligible(self):
        for update in (
            {"start": date(2026, 9, 2)},
            {"metric": "avg_expense"},
            {"kind": "detail"},
            {"group_by": "category"},
        ):
            self.assertIsNone(
                compare_period(self.plan.model_copy(update=update), self.ledger, "u")
            )
