"""Regression coverage for R7: read-only projections without purchases."""

import asyncio
import copy
import unittest

from demo.planning import build_chat_plan


class ScopedLedger:
    def __init__(self):
        self.rows = {
            "alice": [
                {
                    "account_id": "acc_main",
                    "account_name": "主账户",
                    "balance_cents": 900000,
                }
            ],
            "bob": [
                {
                    "account_id": "acc_main",
                    "account_name": "主账户",
                    "balance_cents": 1200000,
                }
            ],
        }
        self.reads = []

    def account_snapshot(self, user):
        self.reads.append(user)
        return copy.deepcopy(self.rows[user])


def run(question, previous=None, provider=None, ledger=None, user="alice"):
    return asyncio.run(
        build_chat_plan(question, ledger or ScopedLedger(), user, provider, previous)
    )


class RecheckPlanningTests(unittest.TestCase):
    def test_exact_checklist_projection_defaults_are_disclosed_and_deterministic(self):
        card = run("模拟未来30天余额，不要改账本")
        assert card["status"] == "simulated"
        assert card == run("模拟未来30天余额，不要改账本")
        assert card["result"]["assumptions"]["goal_cents"] == 0
        assert card["result"]["assumptions"]["floor_cents"] == 0
        assert len(card["result"]["points_cents"]) == 30
        assert not any(e["source"] == "computer_goal" for e in card["result"]["events"])
        assert any("默认" in a and "0元" in a for a in card["assumptions"])
        assert card["earliest_purchase_day"] is None

    def test_salary_delay_without_purchase_and_explicit_floor(self):
        card = run("工资晚五天到账，保留3000元")
        assert card["status"] == "simulated"
        assert card["result"]["assumptions"]["goal_cents"] == 0
        assert card["result"]["assumptions"]["floor_cents"] == 300000
        assert [
            e["day"] for e in card["result"]["events"] if e["source"] == "salary"
        ] == [20]

    def test_explicit_projection_overrides_previous_purchase_even_with_model_goal(self):
        class Provider:
            provider = "fake"

            async def json_completion(self, system, message):
                return {"goal_cents": 600000, "floor_cents": None, "horizon_days": 90}

        previous = {"goal_cents": 600000, "floor_cents": 300000}
        original = copy.deepcopy(previous)
        card = run("只模拟未来90天余额，不购买", previous, Provider())
        assert card["status"] == "simulated"
        assert card["result"]["assumptions"]["goal_cents"] == 0
        assert len(card["result"]["points_cents"]) == 90
        assert not any(e["source"] == "computer_goal" for e in card["result"]["events"])
        assert previous == original
        assert any("30天周期" in a for a in card["assumptions"])

    def test_purchase_parameters_and_authorized_snapshot_remain_isolated(self):
        ledger = ScopedLedger()
        before = copy.deepcopy(ledger.rows)
        purchase = run("买6000元电脑，保留3000元", ledger=ledger)
        assert purchase["result"]["assumptions"]["goal_cents"] == 600000
        assert purchase["result"]["assumptions"]["floor_cents"] == 300000
        projection = run("模拟未来30天余额，不要改账本", ledger=ledger, user="bob")
        assert projection["result"]["assumptions"]["opening_balance_cents"] == 1200000
        assert ledger.rows == before
        assert ledger.reads == ["alice", "bob"]
