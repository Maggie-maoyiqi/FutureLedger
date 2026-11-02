"""Executable examples for docs/功能检验清单.md.

These are intentionally phrased like the questions a classmate can type into
the local demo.  The test injects an explicit deterministic test provider; user startup still requires an LLM.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from demo.api import create_app
from demo.provider import ChatProvider


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/benchmark.sqlite"


class FeatureExampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = Path(self.temp.name) / "fixture.sqlite"
        shutil.copy2(FIXTURE, db)
        self.client = TestClient(create_app(db_path=db, provider=ChatProvider(provider="local", allow_local=True), today=date(2026, 10, 26)))
        for username in ("test_user", "isolation_user"):
            self.client.post(
                "/api/auth/register",
                json={"username": username, "password": "test-password-123"},
            )
        token = self.client.post(
            "/api/auth/login",
            json={"username": "test_user", "password": "test-password-123"},
        ).json()["auth_token"]
        self.session = self.client.post(
            "/api/sessions", json={"auth_token": token}
        ).json()["session_id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def ask(self, question: str) -> dict:
        response = self.client.post(
            "/api/ask", json={"session_id": self.session, "question": question}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_documented_query_examples(self):
        cases = (
            ("这个月的支出是多少？", "metric_card"),
            ("上个月的收入明细", "table"),
            ("2026年9月各类别净支出排名前五", "bar"),
            ("2026年每月餐饮净支出趋势", "line"),
            ("上个月餐饮平均每笔消费是多少？", "metric_card"),
            ("本月转账记录", "table"),
        )
        for question, chart in cases:
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertEqual(result["status"], "answer")
                self.assertEqual(result["chart"], chart)

    def test_documented_route_and_safety_examples(self):
        planning = self.ask("我想存6000元买电脑，至少留3000元")
        self.assertEqual(planning["status"], "simulated")
        self.assertEqual(planning["route"], "planning")
        self.assertEqual(len(planning['result']['points_cents']), 30)
        self.assertEqual(planning['result']['assumptions']['goal_cents'], 600000)
        self.assertTrue(planning['assumptions'])

        blocked = self.ask("DROP TABLE transactions")
        self.assertEqual(blocked["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
