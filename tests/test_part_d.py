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

    def test_outgoing_transfer_counts_as_demo_outflow(self):
        from demo.query import Ledger

        result = Ledger(self.db).execute(self._plan(metric="gross_expense", category="转账"), "test_user")

        # Formal C gold retains consumption-only semantics; the interactive
        # ledger follows the checklist's outflow semantics independently.
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            expected = conn.execute(
                "SELECT id,amount_cents FROM transactions WHERE user_id=? "
                "AND account_id IN (SELECT id FROM accounts WHERE user_id=?) "
                "AND status='POSTED' AND category='转账' AND kind='transfer' "
                "AND direction='OUT' AND substr(posted_at,1,10)>=? "
                "AND substr(posted_at,1,10)<? ORDER BY id",
                ('test_user', 'test_user', '2026-09-01', '2026-10-01'),
            ).fetchall()
        self.assertTrue(expected)
        self.assertEqual(result["rows"], [{"value_cents": sum(row[1] for row in expected)}])
        self.assertEqual(result["evidence_ids"], [row[0] for row in expected])

    def test_income_excludes_outgoing_transfer(self):
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

        from demo.provider import ChatProvider
        app = create_app(db_path=self.db, provider=ChatProvider(provider="local", allow_local=True))
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


class BenchmarkAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        cls.root = Path(__file__).resolve().parents[1]
        cls.db = cls.root / "data/benchmark.sqlite"

    def test_all_analysis_cases_match_production_ledger(self):
        import json

        from demo.query import Ledger
        from demo.semantic import IntentPlan

        ledger = Ledger(self.db)
        cases = json.loads((self.root / "benchmark/analysis_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                raw = case["plan"].copy()
                raw["kind"] = "aggregate"
                raw["sort"] = "metric_desc"
                raw["filters"] = {
                    "category": raw["filters"]["category"],
                    "merchant": None,
                    "account_id": None,
                }
                plan = IntentPlan.model_validate_json(json.dumps(raw, ensure_ascii=False))
                actual = ledger.execute(plan, case["user_id"])
                self.assertEqual(actual["rows"][0]["value_cents"], case["expected"]["value_cents"])
                self.assertEqual(actual["evidence_ids"], case["expected"]["evidence_ids"])

    def test_all_cashflow_cases_match_production_simulator(self):
        import json

        from demo.cashflow import simulate_cashflow

        cases = json.loads((self.root / "benchmark/cashflow_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                actual = simulate_cashflow(
                    salary_day=case["salary_day"],
                    purchase_day=case["purchase_day"],
                )
                expected = case["expected"]
                self.assertEqual(list(actual.points_cents), expected["points_cents"])
                self.assertEqual(actual.min_balance_cents, expected["min_balance_cents"])
                self.assertEqual(actual.min_day, expected["min_day"])
                self.assertEqual(actual.feasible, expected["feasible"])

    def test_production_modules_do_not_import_reference_engine(self):
        for relative in ("demo/cashflow.py", "demo/query.py", "demo/api.py"):
            with self.subTest(module=relative):
                source = (self.root / relative).read_text(encoding="utf-8")
                self.assertNotIn("reference_engine", source)


if __name__ == "__main__":
    unittest.main()
