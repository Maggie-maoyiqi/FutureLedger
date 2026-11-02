import asyncio
import unittest

from demo.planning import build_chat_plan


class Ledger:
    def account_snapshot(self, user):
        return [
            {"account_id": "acc_main", "account_name": "主账户", "balance_cents": 900000},
            {"account_id": "acc_savings", "account_name": "储蓄账户", "balance_cents": 200000},
        ]


def plan(question, previous=None):
    return asyncio.run(build_chat_plan(question, Ledger(), "alice", None, previous))


class FinalPlanningParametersTests(unittest.TestCase):
    def test_independent_delay_and_horizon_in_either_order(self):
        for question in ("工资晚5天，模拟未来90天余额，不购买", "模拟未来90天余额，不购买，工资晚5天"):
            with self.subTest(question=question):
                card = plan(question)
                self.assertEqual(card["status"], "simulated")
                self.assertEqual(len(card["result"]["points_cents"]), 90)
                self.assertEqual([e["day"] for e in card["result"]["events"] if e["source"] == "salary"], [20, 50, 80])

    def test_parameter_followups(self):
        previous = {"goal_cents": 600000, "floor_cents": 300000}
        for question, key, expected in (("目标改为1000元", "goal_cents", 100000), ("每天花100元", "daily_budget_cents", 10000), ("至少留4000元", "floor_cents", 400000)):
            with self.subTest(question=question):
                self.assertEqual(plan(question, previous)["_planning_context"][key], expected)

    def test_invalid_explicit_money_is_never_partially_parsed(self):
        for amount in ("-100", "100.001", "1,000", "+100", "一千", ".50"):
            with self.subTest(amount=amount):
                self.assertEqual(plan("目标改为" + amount + "元", {"goal_cents": 600000, "floor_cents": 300000})["status"], "clarification")

    def test_account_confirmation_retains_context(self):
        first = plan("用储蓄账户模拟未来90天余额，不购买，每天花100元")
        self.assertEqual(first["status"], "clarification")
        card = plan("确认使用主账户", first["_planning_context"])
        self.assertEqual(card["status"], "simulated")
        self.assertEqual(len(card["result"]["points_cents"]), 90)
        self.assertEqual(card["result"]["assumptions"]["daily_budget_cents"], 10000)

    def test_named_nonmain_account_requires_clarification(self):
        for name in ("储蓄账户", "acc_savings", "工资账户", "信用卡账户"):
            with self.subTest(name=name):
                self.assertEqual(plan("用" + name + "模拟未来30天余额，不购买")["status"], "clarification")
