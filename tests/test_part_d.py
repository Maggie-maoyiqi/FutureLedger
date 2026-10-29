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


class SimulationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        cls.root = Path(__file__).resolve().parents[1]
        cls.db = cls.root / "data/benchmark.sqlite"
        cls.base_request = {
            "session_id": "test-session",
            "plan_version": 1,
            "salary_day": 17,
            "purchase_day": 19,
            "daily_budget_cents": 8000,
            "floor_cents": 300000,
            "goal_cents": 600000,
        }

    def test_simulation_requires_valid_session(self):
        from fastapi import HTTPException
        from demo.api import PlanSimulationRequest, create_app

        app = create_app(db_path=self.db)
        route = next(route for route in app.routes if route.path == "/api/plans/{plan_id}/simulate")
        request = PlanSimulationRequest.model_validate({**self.base_request, "session_id": "invalid-session"})

        with self.assertRaises(HTTPException) as caught:
            route.endpoint("goal-demo", request)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["code"], "FORBIDDEN")

    def test_simulation_returns_anchor_result(self):
        from demo.api import PlanSimulationRequest, simulate_plan_payload

        request = PlanSimulationRequest.model_validate(self.base_request)
        payload = simulate_plan_payload("goal-demo", request, user_id="test_user")
        self.assertEqual(payload["status"], "simulated")
        self.assertEqual(payload["plan_id"], "goal-demo")
        self.assertEqual(payload["result"]["min_balance_cents"], 412200)
        self.assertEqual(payload["result"]["min_day"], 16)
        self.assertTrue(payload["result"]["feasible"])
        self.assertEqual(len(payload["result"]["points_cents"]), 30)

    def test_simulation_rejects_extra_fields_and_invalid_values(self):
        from pydantic import ValidationError
        from demo.api import PlanSimulationRequest

        with self.assertRaises(ValidationError):
            PlanSimulationRequest.model_validate({**self.base_request, "unexpected": "must be rejected"})
        with self.assertRaises(ValidationError):
            PlanSimulationRequest.model_validate({**self.base_request, "salary_day": 31})

    def test_simulation_does_not_write_ledger(self):
        import sqlite3
        from demo.api import PlanSimulationRequest, simulate_plan_payload

        with sqlite3.connect(self.db) as conn:
            before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            balances_before = conn.execute("SELECT id, balance_cents FROM accounts ORDER BY id").fetchall()

        simulate_plan_payload(
            "goal-demo",
            PlanSimulationRequest.model_validate(self.base_request),
            user_id="test_user",
        )

        with sqlite3.connect(self.db) as conn:
            after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            balances_after = conn.execute("SELECT id, balance_cents FROM accounts ORDER BY id").fetchall()
        self.assertEqual(after, before)
        self.assertEqual(balances_after, balances_before)


if __name__ == "__main__":
    unittest.main()
