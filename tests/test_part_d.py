"""Part D tests for deterministic analytics and cash-flow behavior."""

import unittest


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


if __name__ == "__main__":
    unittest.main()
