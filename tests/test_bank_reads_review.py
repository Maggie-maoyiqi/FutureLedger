import unittest
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.agent.auth import DevelopmentTokens, Principal
from services.api.execution import ExecutionGateAdapter, ExecutionService, RoleABankAdapter


class BankReadsReviewTests(unittest.TestCase):
    def test_agent_transfer_uses_real_read_port_and_waits_for_confirmation(self):
        from services.api.agent.bank_reads import RoleABankReads

        class Bank:
            def __init__(self): self.calls = []
            def get_balance(self, subject, account):
                self.calls.append(('balance', subject, account))
                return {'balance_cents': 900000}
            def resolve_recipient(self, subject, recipient):
                self.calls.append(('recipient', subject, recipient))
                return {'recipient_id': recipient, 'verified': True}
            def quote_transfer(self, subject, request, now):
                self.calls.append(('quote', subject))
                return {'fee_cents': 0}

        bank = Bank()
        service = ExecutionService(ExecutionService.connect(), RoleABankAdapter(bank),
                                   clock=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.addCleanup(service.db.close)
        gate = ExecutionGateAdapter(service)
        principal = Principal('test_user', ('acc_daily', 'acc_savings'), ('personal_ledger',))
        app = create_app(authenticator=DevelopmentTokens({'token': principal}),
            bank_read_adapter=RoleABankReads(bank, request_factory=dict), execution=gate, execution_gate_adapter=gate,
            clock=lambda: date(2026, 11, 1))
        with TestClient(app) as client:
            headers = {'Authorization': 'Bearer token'}
            sid = client.post('/api/sessions', headers=headers, json={}).json()['data']['session_id']
            response = client.post('/api/agent/chat', headers=headers,
                json={'session_id': sid, 'text': '从acc_daily给妈妈转25元，今天'})
            self.assertEqual(response.status_code, 200, response.text)
            report = response.json()['data']
            self.assertEqual(report['execution_status'], 'AWAITING_CONFIRMATION', report)
            self.assertEqual([item[0] for item in bank.calls], ['balance', 'recipient', 'quote'])
            self.assertEqual(service.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
