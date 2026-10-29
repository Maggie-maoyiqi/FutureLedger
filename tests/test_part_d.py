"""Part D tests for deterministic analytics and cash-flow behavior."""

import unittest
from datetime import date


class CashflowCoreTests(unittest.TestCase):
    def test_cashflow_matches_baseline_anchor(self):
        from demo.cashflow import simulate_cashflow

        result = simulate_cashflow(salary_day=12, purchase_day=13)

        self.assertEqual(result.points_cents[10], 452200)
        self.assertEqual(result.points_cents[-1], 500200)
        self.assertEqual(
            (result.min_balance_cents, result.min_day, result.feasible),
            (452200, 11, True),
        )

    def test_cashflow_delayed_salary_is_infeasible(self):
        from demo.cashflow import simulate_cashflow

        result = simulate_cashflow(salary_day=17, purchase_day=13)

        self.assertEqual(
            (result.min_balance_cents, result.min_day, result.feasible),
            (-187800, 16, False),
        )
        self.assertTrue(result.conflicts)

    def test_cashflow_replan_after_delayed_salary(self):
        from demo.cashflow import simulate_cashflow

        result = simulate_cashflow(salary_day=17, purchase_day=19)

        self.assertEqual(
            (result.min_balance_cents, result.min_day, result.feasible),
            (412200, 16, True),
        )

    def test_cashflow_rejects_invalid_days_and_budget(self):
        from demo.cashflow import simulate_cashflow

        for salary_day, purchase_day in ((0, 13), (31, 13), (12, 0), (12, 31)):
            with self.subTest(salary_day=salary_day, purchase_day=purchase_day):
                with self.assertRaises(ValueError):
                    simulate_cashflow(salary_day=salary_day, purchase_day=purchase_day)
        with self.assertRaises(ValueError):
            simulate_cashflow(salary_day=12, purchase_day=13, daily_budget_cents=-1)
        with self.assertRaises(ValueError):
            simulate_cashflow(salary_day=True, purchase_day=13)


class AnalyticsBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        cls.root = Path(__file__).resolve().parents[1]
        cls.db = cls.root / "data/benchmark.sqlite"

    def _plan(self, *, metric, category, start="2026-09-01", end="2026-10-01"):
        from demo.semantic import IntentPlan

        return IntentPlan.model_validate({
            "kind": "aggregate",
            "dataset_id": "personal_ledger",
            "semantic_version": 1,
            "metric": metric,
            "start": date.fromisoformat(start),
            "end_exclusive": date.fromisoformat(end),
            "filters": {"category": category, "merchant": None, "account_id": None},
            "group_by": None,
            "sort": "metric_desc",
            "limit": 20,
        })

    def test_transfer_is_not_consumption(self):
        from demo.query import Ledger

        result = Ledger(self.db).execute(self._plan(metric="gross_expense", category="转账"), "test_user")

        self.assertEqual(result["rows"], [{"value_cents": 0}])
        self.assertEqual(result["evidence_ids"], [])

    def test_income_excludes_refund_and_internal_transfer(self):
        from demo.query import Ledger

        result = Ledger(self.db).execute(self._plan(metric="income", category="转账"), "test_user")

        self.assertEqual(result["rows"], [{"value_cents": 0}])
        self.assertEqual(result["evidence_ids"], [])

    def test_failed_and_pending_are_excluded(self):
        import sqlite3

        from demo.query import Ledger

        result = Ledger(self.db).execute(self._plan(metric="gross_expense", category="购物"), "test_user")

        self.assertTrue(result["evidence_ids"])
        with sqlite3.connect(self.db) as conn:
            statuses = [
                row[0]
                for row in conn.execute(
                    "SELECT status FROM transactions WHERE id IN (%s)"
                    % ",".join("?" for _ in result["evidence_ids"]),
                    result["evidence_ids"],
                )
            ]
        self.assertTrue(all(status == "POSTED" for status in statuses))

    def test_evidence_is_sorted_and_scope_isolated(self):
        from demo.query import Ledger

        ledger = Ledger(self.db)
        own = ledger.execute(self._plan(metric="net_expense", category="餐饮"), "test_user")
        other = ledger.execute(self._plan(metric="net_expense", category="餐饮"), "isolation_user")

        self.assertEqual(own["evidence_ids"], sorted(own["evidence_ids"]))
        self.assertTrue(set(own["evidence_ids"]).isdisjoint(other["evidence_ids"]))

    def test_empty_analysis_returns_zero(self):
        from demo.query import Ledger

        result = Ledger(self.db).execute(
            self._plan(metric="net_expense", category="餐饮", start="2030-01-01", end="2030-02-01"),
            "test_user",
        )

        self.assertEqual(result, {"rows": [{"value_cents": 0}], "evidence_ids": [], "sql": result["sql"]})


if __name__ == "__main__":
    unittest.main()
