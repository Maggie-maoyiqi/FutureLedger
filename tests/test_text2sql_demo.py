"""Contract and runtime checks for the read-only demo."""

import json
import tempfile
import shutil
import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from demo.api import create_app, requested_balance_account
from demo.provider import ChatProvider, ProviderError
from demo.query import Ledger, QueryError, audit_sql
from demo.semantic import IntentPlan


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/benchmark.sqlite"


def plan(**overrides):
    raw = {
        "kind": "aggregate", "dataset_id": "personal_ledger", "semantic_version": 1,
        "metric": "net_expense", "start": "2026-09-01", "end_exclusive": "2026-10-01",
        "filters": {"category": "餐饮", "merchant": None, "account_id": None},
        "group_by": None, "sort": "metric_desc", "limit": 20,
    }
    raw.update(overrides)
    return raw


class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB.is_file():
            raise RuntimeError("Run python3 scripts/generate_data.py before demo tests")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        source = Path(self.temp.name) / "fixture.sqlite"
        shutil.copy2(DB, source)
        self.client = TestClient(create_app(db_path=source, provider=ChatProvider(provider="local", allow_local=True), today=date(2026, 10, 26)))
        for user in ("test_user", "isolation_user"):
            response = self.client.post("/api/auth/register", json={"username": user, "password": "test-password-123"})
            self.assertEqual(response.status_code, 200, response.text)
        auth = self.client.post("/api/auth/login", json={"username": "test_user", "password": "test-password-123"}).json()["auth_token"]
        self.other_auth = self.client.post("/api/auth/login", json={"username": "isolation_user", "password": "test-password-123"}).json()["auth_token"]
        self.session = self.client.post("/api/sessions", json={"auth_token": auth}).json()["session_id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def ask(self, question, session=None):
        response = self.client.post("/api/ask", json={"session_id": session or self.session, "question": question})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_api_shapes_and_routes(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ready")
        self.assertEqual(self.client.get("/api/semantic").json()["dataset_id"], "personal_ledger")
        self.assertIn("FutureLedger", self.client.get("/").text)
        self.assertEqual(self.ask("你好")["status"], "view")
        self.assertEqual(self.ask("2026 年 9 月天气怎么样")["status"], "view")
        self.assertEqual(self.ask("2026 年 9 月各类别排名前五")["status"], "clarification")
        self.assertEqual(self.ask("把钱转给朋友")["status"], "clarification")
        self.assertEqual(self.ask("DROP TABLE transactions")["status"], "blocked")
        self.assertEqual(self.ask("删除所有流水")["status"], "blocked")

    def test_home_serves_unified_dashboard_with_agent_and_login(self):
        html = self.client.get("/").text
        nav = html.split('<nav id="nav"', 1)[1].split("</nav>", 1)[0]
        destinations = (
            "银行总览", "账单分析", "订阅与代扣", "未来账本",
            "任务中心", "个人偏好", "评审工作台", "智能体小智",
        )
        positions = [nav.index(label) for label in destinations]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('id="assistantRail"', html)
        self.assertIn("未来助手", html)
        self.assertIn('id="authGate"', html)
        self.assertIn("function agentPage", html)
        self.assertIn("/api/auth/login", html)
        self.assertIn("/api/sessions", html)
        self.assertIn("/api/ask", html)
        self.assertIn("/api/actions/", html)
        self.assertIn("/api/queries/", html)

    def test_aggregation_ranking_trend_detail_average(self):
        scalar = self.ask("2026 年 9 月餐饮净支出是多少？")
        self.assertEqual(scalar["status"], "answer")
        self.assertEqual(scalar["chart"], "metric_card")
        self.assertIn("¥", scalar["answer"])
        rank = self.ask("2026 年 9 月各类别净支出排名前五")
        self.assertEqual(rank["chart"], "bar")
        self.assertEqual(len(rank["rows"]), 5)
        self.assertGreaterEqual(rank["rows"][0]["value_cents"], rank["rows"][1]["value_cents"])
        trend = self.ask("2026 年每月餐饮净支出趋势")
        self.assertEqual(trend["chart"], "line")
        self.assertEqual([x["group_key"] for x in trend["rows"]], sorted(x["group_key"] for x in trend["rows"]))
        detail = self.ask("2026 年 9 月餐饮消费明细")
        self.assertEqual(detail["chart"], "table")
        self.assertEqual(len(detail["rows"]), 20)
        self.assertTrue(all(row["category"] == "餐饮" for row in detail["rows"]))
        self.assertNotIn("id", detail["rows"][0])
        mean = self.ask("上个月餐饮平均每笔消费是多少？")
        self.assertIsInstance(mean["rows"][0]["value_cents"], float)

    def test_model_infers_year_and_grouping_from_meaning_with_current_date_context(self):
        class CapturingProvider:
            provider = "compatible"

            def __init__(self):
                self.calls = []

            async def json_completion(self, system, user):
                self.calls.append((system, json.loads(user)))
                if len(self.calls) == 1:
                    return {"route": "query", "message": None}
                return {"route": "analysis", "plan": plan(
                    metric="net_expense", start="2026-08-01", end_exclusive="2026-10-01",
                    filters={"category": None, "merchant": None, "account_id": None},
                    group_by="month", series_by="category", sort="date_asc",
                )}

        provider = CapturingProvider()
        client, sid = self._model_client(provider)
        question = "8月和9月的各项净支出"
        response = client.post("/api/ask", json={"session_id": sid, "question": question}).json()
        self.assertEqual(response["status"], "answer")
        self.assertEqual({row["group_key"] for row in response["rows"]}, {"2026-08", "2026-09"})
        self.assertEqual(provider.calls[1][1]["question"], question)
        self.assertIn("current_user_data", provider.calls[1][1])
        self.assertIn('"today": "2026-10-26"', provider.calls[1][0])
        self.assertIn("most recent plausible occurrence", provider.calls[1][0])
        self.assertIn("group_by/series_by", provider.calls[1][0])

    def test_180_reference_cases_match_amount_and_evidence(self):
        ledger = Ledger(DB)
        for case in json.loads((ROOT / "benchmark/analysis_cases.json").read_text()):
            raw = case["plan"].copy()
            raw["kind"] = "aggregate"
            raw["sort"] = "metric_desc"
            raw["filters"] = {"category": raw["filters"]["category"], "merchant": None, "account_id": None}
            parsed = IntentPlan.model_validate_json(json.dumps(raw, ensure_ascii=False))
            result = ledger.execute(parsed, case["user_id"])
            with self.subTest(case=case["id"]):
                self.assertEqual(result["rows"][0]["value_cents"], case["expected"]["value_cents"])
                self.assertEqual(result["evidence_ids"], case["expected"]["evidence_ids"])

    def test_scope_plan_and_sql_guards(self):
        ledger = Ledger(DB)
        other = ledger.accounts("isolation_user")[0]
        p = IntentPlan.model_validate_json(json.dumps(plan(filters={"category": "餐饮", "merchant": None, "account_id": other})))
        with self.assertRaises(QueryError) as caught:
            ledger.execute(p, "test_user")
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        unknown = IntentPlan.model_validate_json(json.dumps(plan(filters={"category": "不存在的类别", "merchant": None, "account_id": None})))
        with self.assertRaises(QueryError) as caught:
            ledger.execute(unknown, "test_user")
        self.assertEqual(caught.exception.code, "UNKNOWN_ENUM")
        with self.assertRaises(Exception):
            IntentPlan.model_validate_json(json.dumps(plan(sql="DELETE FROM transactions")))
        with self.assertRaises(Exception):
            IntentPlan.model_validate_json(json.dumps(plan(user_id="isolation_user")))
        with self.assertRaises(QueryError):
            audit_sql("DELETE FROM transactions")
        with self.assertRaises(QueryError):
            audit_sql("SELECT * FROM transactions; DROP TABLE transactions")

    def test_model_plan_cannot_override_scope_or_drop_unknown_filter(self):
        class FakeProvider:
            provider = "compatible"

            def __init__(self, *results):
                self.results = list(results)

            async def json_completion(self, system, user):
                return self.results.pop(0)

        candidate = plan()
        candidate["user_id"] = "isolation_user"
        client, sid = self._model_client(FakeProvider({"route": "query", "message": None}, {"route": "analysis", "plan": candidate}))
        response = client.post("/api/ask", json={"session_id": sid, "question": "2026 年 9 月餐饮净支出"}).json()
        self.assertEqual(response["status"], "clarification")
        candidate = plan(filters={"category": "未知分类", "merchant": None, "account_id": None})
        client, sid = self._model_client(FakeProvider({"route": "query", "message": None}, {"route": "analysis", "plan": candidate}))
        response = client.post("/api/ask", json={"session_id": sid, "question": "2026 年 9 月未知分类消费"}).json()
        self.assertEqual(response["code"], "UNKNOWN_ENUM")
        self.assertEqual(response["status"], "clarification")

    def test_export_is_session_scoped(self):
        answer = self.ask("2026 年 9 月餐饮净支出是多少？")
        qid = answer["query_id"]
        own = self.client.get(f"/api/queries/{qid}/export.csv", params={"session_id": self.session})
        self.assertEqual(own.status_code, 200)
        self.assertIn("金额（分）", own.text)
        second = self.client.post("/api/sessions", json={"auth_token": self.other_auth}).json()["session_id"]
        denied = self.client.get(f"/api/queries/{qid}/export.csv", params={"session_id": second})
        self.assertEqual(denied.status_code, 404)

    def test_clarification_followup_keeps_dataset_and_prior_question(self):
        first = self.ask("2026 年 9 月各类别排名前五")
        self.assertEqual(first["status"], "clarification")
        second = self.ask("按净支出")
        self.assertEqual(second["status"], "answer")
        self.assertEqual(len(second["rows"]), 5)

    def test_transfer_requires_password_then_category_and_counts_as_inflow_outflow(self):
        expense_before = self.ask("本月支出")["rows"][0]["value_cents"]
        preview = self.ask("给测试用户B转 25 元")
        self.assertEqual(preview["status"], "confirmation_required")
        action_id = preview["action_id"]
        denied = self.client.post(f"/api/actions/{action_id}/authorize", json={
            "session_id": self.session, "password": "wrong-password-123",
        })
        self.assertEqual(denied.status_code, 401)
        authorized = self.client.post(f"/api/actions/{action_id}/authorize", json={
            "session_id": self.session, "password": "test-password-123",
        })
        self.assertEqual(authorized.status_code, 200)
        authorization_id = authorized.json()["authorization_id"]
        recipient_session = self.client.post("/api/sessions", json={"auth_token": self.other_auth}).json()["session_id"]
        done = self.client.post(f"/api/actions/{action_id}/confirm", json={
            "session_id": self.session, "authorization_id": authorization_id, "category": "购物",
        })
        self.assertEqual(done.status_code, 200, done.text)
        self.assertIn("购物", done.json()["message"])
        outgoing = self.client.post("/api/ask", json={"session_id": self.session, "question": "本月的支出明细"}).json()
        incoming = self.client.post("/api/ask", json={"session_id": recipient_session, "question": "本月的收入明细"}).json()
        expense_after = self.ask("本月支出")["rows"][0]["value_cents"]
        recipient_income = self.client.post("/api/ask", json={"session_id": recipient_session, "question": "本月收入"}).json()
        self.assertEqual(expense_after - expense_before, 2500)
        self.assertEqual(recipient_income["rows"][0]["value_cents"], 2500)
        self.assertTrue(any(r["kind"] == "transfer" and r["category"] == "购物" and r["direction"] == "OUT" for r in outgoing["rows"]))
        self.assertTrue(any(r["kind"] == "transfer" and r["direction"] == "IN" for r in incoming["rows"]))

    def test_account_snapshot_and_balance_answers_follow_transfer(self):
        before = self.client.get("/api/accounts", params={"session_id": self.session})
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(
            {row["account_id"]: row["balance_cents"] for row in before.json()["accounts"]},
            {"acc_daily": 900000, "acc_savings": 500000},
        )
        savings = self.ask("我的储蓄账户有多少钱？")
        self.assertEqual(savings["status"], "answer")
        self.assertEqual(savings["rows"], [{
            "account_name": "储蓄账户", "balance_cents": 500000,
        }])
        self.assertIn("¥5,000.00", savings["answer"])
        account_list = self.ask("我有几个银行账户？")
        self.assertEqual(account_list["status"], "answer")
        self.assertEqual(len(account_list["rows"]), 2)

        preview = self.ask("给测试用户B转 25 元")
        authorized = self.client.post(f"/api/actions/{preview['action_id']}/authorize", json={
            "session_id": self.session, "password": "test-password-123",
        }).json()
        done = self.client.post(f"/api/actions/{preview['action_id']}/confirm", json={
            "session_id": self.session, "authorization_id": authorized["authorization_id"], "category": "购物",
        })
        self.assertEqual(done.status_code, 200, done.text)

        after = self.client.get("/api/accounts", params={"session_id": self.session})
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()["total_balance_cents"], 1397500)
        self.assertEqual(
            {row["account_id"]: row["balance_cents"] for row in after.json()["accounts"]},
            {"acc_daily": 897500, "acc_savings": 500000},
        )
        all_balances = self.ask("我的银行账户余额是多少？")
        self.assertIn("¥13,975.00", all_balances["answer"])
        self.assertEqual(
            {row["account_name"]: row["balance_cents"] for row in all_balances["rows"]},
            {"主账户": 897500, "储蓄账户": 500000},
        )

    def test_balance_detection_does_not_preempt_transactions_or_transfers(self):
        self.assertEqual(requested_balance_account("上个月主账户支出金额是多少？"), (False, None))
        self.assertEqual(requested_balance_account("从我的账户给测试用户B转25元，转完告诉我余额"), (False, None))
        self.assertEqual(requested_balance_account("2026年9月底主账户余额是多少？"), (False, None))
        self.assertEqual(requested_balance_account("昨天我的主账户余额是多少？"), (False, None))
        self.assertEqual(requested_balance_account("去年储蓄账户余额是多少？"), (False, None))
        self.assertEqual(requested_balance_account("主账户和储蓄账户分别有多少钱？"), (True, None))

        both = self.ask("主账户和储蓄账户分别有多少钱？")
        self.assertEqual(
            {row["account_name"] for row in both["rows"]},
            {"主账户", "储蓄账户"},
        )

    def test_missing_fixture_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            client = TestClient(create_app(db_path=Path(folder) / "missing.sqlite", provider=ChatProvider(provider="local", allow_local=True)))
            self.assertEqual(client.get("/api/health").json()["status"], "fixture_missing")
            self.assertEqual(client.post("/api/sessions", json={"auth_token": "missing"}).status_code, 401)

    def _model_client(self, provider):
        source = Path(self.temp.name) / f"model-fixture-{id(provider)}.sqlite"
        shutil.copy2(DB, source)
        client = TestClient(create_app(db_path=source, provider=provider, today=date(2026, 10, 26)))
        for user in ("test_user", "isolation_user"):
            client.post("/api/auth/register", json={"username": user, "password": "test-password-123"})
        auth = client.post("/api/auth/login", json={"username": "test_user", "password": "test-password-123"}).json()["auth_token"]
        session = client.post("/api/sessions", json={"auth_token": auth}).json()["session_id"]
        return client, session


class ProviderTest(unittest.IsolatedAsyncioTestCase):
    def test_startup_requires_explicit_llm_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "FUTURELEDGER_PROVIDER"):
                ChatProvider()
        with patch.dict("os.environ", {"FUTURELEDGER_PROVIDER": "deepseek"}, clear=True):
            with self.assertRaisesRegex(ValueError, "FUTURELEDGER_API_KEY"):
                ChatProvider()
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "test-only"):
                ChatProvider(provider="local")

    async def test_openai_compatible_request_and_response(self):
        observed = {}

        def responder(request):
            observed["url"] = str(request.url)
            observed["auth"] = request.headers["authorization"]
            observed["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"route":"unrelated","message":"ok"}'}}]})

        provider = ChatProvider(provider="deepseek", key="fake-test-key", model="demo-model", transport=httpx.MockTransport(responder))
        result = await provider.json_completion("JSON only", "hello")
        self.assertEqual(result["route"], "unrelated")
        self.assertEqual(observed["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(observed["auth"], "Bearer fake-test-key")
        self.assertEqual(observed["body"]["response_format"], {"type": "json_object"})

    async def test_provider_failures_do_not_expose_secret(self):
        provider = ChatProvider(provider="openai", key="fake-secret", model="demo-model", transport=httpx.MockTransport(lambda _: httpx.Response(401)))
        with self.assertRaises(ProviderError) as caught:
            await provider.json_completion("JSON", "hello")
        self.assertNotIn("fake-secret", str(caught.exception))

    async def test_provider_retries_truncated_json_with_larger_output_limit(self):
        limits = []

        def responder(request):
            body = json.loads(request.content)
            limits.append(body["max_tokens"])
            if len(limits) == 1:
                return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": '{"route":'}}]})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"route":"query"}'}}]})

        provider = ChatProvider(provider="deepseek", key="fake-test-key", model="demo-model", transport=httpx.MockTransport(responder))
        result = await provider.json_completion("JSON only", "question")
        self.assertEqual(result["route"], "query")
        self.assertEqual(limits, [2048, 4096])

    async def test_compatible_provider_uses_configured_base_and_model(self):
        observed = {}

        def responder(request):
            observed["url"] = str(request.url)
            observed["model"] = json.loads(request.content)["model"]
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"route":"query"}'}}]})

        provider = ChatProvider(provider="compatible", key="fake-test-key", model="vendor-model-id",
                                base_url="https://vendor.example/v1", transport=httpx.MockTransport(responder))
        result = await provider.json_completion("JSON only", "question")
        self.assertEqual(result["route"], "query")
        self.assertEqual(observed, {"url": "https://vendor.example/v1/chat/completions", "model": "vendor-model-id"})


if __name__ == "__main__":
    unittest.main()
