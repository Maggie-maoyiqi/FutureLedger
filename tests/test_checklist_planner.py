import unittest
import json
from datetime import date
import asyncio
from demo.planner import local_plan, route_question
from demo.planning import build_chat_plan


class ChecklistPlannerTests(unittest.TestCase):
    def test_missing_execution_and_ranking_routes(self):
        self.assertEqual(route_question("给测试用户B转一笔钱")[0], "execution")
        self.assertEqual(route_question("排名前五")[0], "query")

    def test_query_checklist(self):
        today = date(2026, 11, 1)
        assert (
            local_plan("2026年9月各商户支出", today, ())["plan"]["group_by"]
            == "merchant"
        )
        p = local_plan("8月和9月的各项净支出", today, ())["plan"]
        assert (p["start"], p["end_exclusive"]) == ("2026-08-01", "2026-10-01")
        p = local_plan("2026年7月至9月各类别支出变化", today, ())["plan"]
        assert (p["start"], p["end_exclusive"]) == ("2026-07-01", "2026-10-01")
        assert local_plan("最近十笔交易", today, ())["plan"]["limit"] == 10
        assert local_plan("列出本月所有退款", today, ())["plan"]["kind"] == "detail"
        assert (
            local_plan("本月转账收入", today, ())["plan"]["metric"] == "transfer_income"
        )
        assert (
            local_plan("本月转账平均每笔金额", today, ())["plan"]["metric"]
            == "avg_transfer"
        )
        assert local_plan("本月平均支出", today, ())["route"] == "clarification"
        assert (
            local_plan("9月购物支出占全部支出的多少", today, ())["route"]
            == "clarification"
        )


class Ledger:
    def account_snapshot(self, user):
        assert user == "test_user"
        return [
            {"account_id": "main", "account_name": "主账户", "balance_cents": 900000}
        ]


def plan(q, previous=None):
    return asyncio.run(build_chat_plan(q, Ledger(), "test_user", None, previous))


class PlanningTests(unittest.TestCase):
    def test_planning_scope_and_assumptions(self):
        p = plan("我想存6000元买电脑，至少留3000元")
        assert p["status"] == "simulated"
        assert p["result"]["assumptions"]["opening_balance_cents"] == 900000
        assert len(p["result"]["points_cents"]) == 30
        assert p["assumptions"]
        assert len(p["scenarios"]) >= 2

    def test_planning_requires_parameters_and_honest_horizon(self):
        assert plan("每天最多花80元，什么时候可以买电脑？")["status"] == "clarification"
        assert (
            plan("我想在三个月内存6000元，不能让余额低于3000元")["status"]
            == "simulated"
        )
        first = plan("每天最多花80元，什么时候可以买电脑？")
        p = plan("目标6000元，至少留3000元", first["_planning_context"])
        assert p["status"] == "simulated"

    def test_cross_year_and_recent_validation(self):
        from demo.semantic import IntentPlan

        p = local_plan("2025年12月至2026年2月各类别支出", date(2026, 11, 1), ())["plan"]
        self.assertEqual((p["start"], p["end_exclusive"]), ("2025-12-01", "2026-03-01"))
        IntentPlan.model_validate_json(
            json.dumps(local_plan("最近十笔交易", date(2026, 11, 1), ())["plan"])
        )

    def test_provider_validation_and_privacy(self):
        class Provider:
            provider = "fake"

            def __init__(self, raw):
                self.raw = raw
                self.messages = []

            async def json_completion(self, system, message):
                self.messages.append(json.loads(message))
                return self.raw

        for raw in (
            {"goal_cents": 600000, "floor_cents": 300000, "sql": "bad"},
            {"goal_cents": "600000"},
            {"salary_delay_days": 16},
        ):
            provider = Provider(raw)
            result = asyncio.run(
                build_chat_plan("规划", Ledger(), "test_user", provider)
            )
            self.assertEqual(result["status"], "clarification")
            self.assertEqual(provider.messages, [{"question": "规划"}])
        provider = Provider({"goal_cents": 600000, "floor_cents": 300000})
        self.assertEqual(
            asyncio.run(build_chat_plan("规划", Ledger(), "test_user", provider))[
                "status"
            ],
            "simulated",
        )
        self.assertEqual(provider.messages, [{"question": "规划"}])

    def test_account_scope_and_delay(self):
        class ScopedLedger:
            def account_snapshot(self, user):
                self.seen = user
                return [
                    {
                        "account_id": "only",
                        "account_name": "我的账户",
                        "balance_cents": 1234567,
                    }
                ]

        ledger = ScopedLedger()
        context = {"goal_cents": 600000, "floor_cents": 300000}
        p = asyncio.run(
            build_chat_plan("工资晚五天到账", ledger, "other_user", None, context)
        )
        self.assertEqual(ledger.seen, "other_user")
        self.assertEqual(p["result"]["assumptions"]["opening_balance_cents"], 1234567)
        self.assertTrue(
            any(
                e["source"] == "salary" and e["day"] == 20
                for e in p["result"]["events"]
            )
        )
        self.assertEqual(plan("使用储蓄账户", context)["status"], "clarification")
        self.assertEqual(plan("工资晚16天", context)["status"], "clarification")


class RecurringPlanningTests(unittest.TestCase):
    def test_ninety_day_recurring_scenario(self):
        p = plan("三个月内存6000元，至少留3000元")
        self.assertEqual(p["status"], "simulated")
        self.assertEqual(len(p["result"]["points_cents"]), 90)
        events = p["result"]["events"]
        self.assertEqual(
            [e["day"] for e in events if e["source"] == "salary"], [15, 45, 75]
        )
        self.assertEqual(
            [e["day"] for e in events if e["source"] == "computer_goal"], [90]
        )
        self.assertTrue(any("30天周期" in a for a in p["assumptions"]))
        self.assertIn("假设", p["message"])
        self.assertIsInstance(p["earliest_purchase_day"], int)
        self.assertGreaterEqual(p["result"]["min_balance_cents"], 300000)
        self.assertEqual(len(p["scenarios"]), 3)

    def test_negative_cycles_and_scoped_read_only(self):
        class ReadOnlyLedger:
            def account_snapshot(self, user):
                self.user = user
                return [
                    {
                        "account_id": "acc_main",
                        "account_name": "主账户",
                        "balance_cents": 0,
                    }
                ]

        ledger = ReadOnlyLedger()
        result = asyncio.run(
            build_chat_plan("三个月内存6000元，至少留3000元", ledger, "u", None)
        )
        self.assertEqual(result["status"], "simulated")
        self.assertLess(result["result"]["min_balance_cents"], 0)
        self.assertIsNone(result["earliest_purchase_day"])
        self.assertEqual(ledger.user, "u")
        context = {"goal_cents": 600000, "floor_cents": 300000}
        self.assertEqual(plan("使用acc_savings", context)["status"], "clarification")
        self.assertEqual(plan("模拟60天", context)["status"], "clarification")


if __name__ == "__main__":
    unittest.main()
