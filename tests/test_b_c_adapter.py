import asyncio
import sqlite3
import unittest
from datetime import datetime, timezone

from services.api.agent.auth import Principal
from services.api.agent.dag.schemas import ActionProposal, TransferInputs
from services.api.agent.schemas import Constraints
from services.api.execution import ExecutionGateAdapter, ExecutionService, InMemoryBank


class BCApiIntegrationTests(unittest.TestCase):
    def test_c_proposal_uses_b_confirmation_and_worker(self):
        bank = InMemoryBank({("alice", "a"): 100_000, ("rec_1", "r"): 0})
        service = ExecutionService(ExecutionService.connect(), bank,
                                   password_checker=lambda value, user: value == "secret",
                                   clock=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
        adapter = ExecutionGateAdapter(service, worker=service)
        principal = Principal("alice", ("a",), ("personal_ledger",))
        proposal = ActionProposal(
            plan_id="plan-1", plan_version=1, node_id="transfer-1", tool_name="execution.transfer",
            inputs=TransferInputs(source_account_id="a", recipient_id="rec_1", amount_cents=1_000,
                                  execution_date="2026-11-01"), constraints=Constraints())

        binding = asyncio.run(adapter.create_operation(principal, proposal))
        raw = service.status(binding.operation_id, user_id="alice")
        asyncio.run(adapter.confirm_operation(principal, binding,
                                               type("Confirm", (), {"plan_version": 1, "params_hash": raw["params_hash"]})()))
        status = asyncio.run(adapter.get_operation_status(principal, binding))
        self.assertEqual(status.status, "NEEDS_CONFIRM") if False else self.assertEqual(status.status, "READY")
        asyncio.run(adapter.execute_operation(principal, binding,
                                              type("Execute", (), {"plan_version": 1})(), raw["idempotency_key"]))
        final = asyncio.run(adapter.get_operation_status(principal, binding))
        self.assertEqual(final.status, "SUCCEEDED")
        self.assertEqual(final.plan_id, "plan-1")


if __name__ == "__main__":
    unittest.main()
