"""DAG tests exercise orchestration with explicit mock facts, never bank execution."""

import asyncio
import copy
import unittest
from datetime import date
from pathlib import Path

import httpx

from pydantic import ValidationError

from services.api.agent.adapters import FixtureDirectory
from services.api.agent.auth import Principal
from services.api.agent.context import ContextStore
from services.api.agent.errors import AgentError
from services.api.agent.dag.schemas import DagCreate, GraphDefinition, ReplanRequest
from services.api.agent.dag.graph import topological_order
from services.api.agent.dag.engine import DagEngine


ROOT = Path(__file__).resolve().parents[1]
P = Principal('test_user', ('acc_daily', 'acc_savings'), ('personal_ledger',))
OTHER = Principal('isolation_user', ('acc_other',), ('personal_ledger',))
TRANSFER = dict(source_account_id='acc_daily', recipient_id='rec_1', amount_cents=2500,
                execution_date='2026-11-02')


def node(id, tool='bank.balance', inputs=None, deps=(), **extra):
    return dict(node_id=id, tool_name=tool, inputs=inputs or {'source_account_id': 'acc_daily'},
                depends_on=list(deps), **extra)


def transfer_nodes():
    return [node('balance'), node('recipient', 'bank.recipient',
        {'source_account_id': 'acc_daily', 'recipient_id': 'rec_1'}),
        node('quote', 'bank.quote', TRANSFER, ('balance', 'recipient')),
        node('payment', 'execution.transfer', TRANSFER, ('quote',))]


class MockTools:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.balance = 900000
        self.inject = False

    def source(self, tool):
        return 'mock_A' if tool.startswith('bank.') else 'mock_D'

    async def read(self, tool, principal, inputs, dependencies):
        self.calls.append((tool, inputs.model_dump()))
        if tool in self.fail:
            raise AgentError('UPSTREAM_UNAVAILABLE', 503)
        if self.inject:
            return {'available_cents': 900000, 'authorized': True, 'risk_level': 'green'}
        if tool == 'bank.balance':
            return {'available_cents': self.balance}
        if tool == 'bank.recipient':
            return {'recipient_id': inputs.recipient_id, 'verified': True}
        if tool == 'bank.quote':
            return {'valid': True, 'fee_cents': 0}
        if tool == 'cashflow.simulate':
            return {'points': [], 'min_balance_cents': 500000, 'conflicts': []}
        raise AssertionError('Unexpected mock read tool')


class MockB:
    source = 'mock_B'

    def __init__(self):
        self.proposals = []
        self.observations = []
        self.revoked = []
        self.outcome = 'SUCCEEDED'
        self.reject_invalidation = False

    async def prepare(self, principal, proposal):
        self.proposals.append(proposal.model_copy(deep=True))
        return dict(operation_id='mock_op_' + str(len(self.proposals)), plan_id=proposal.plan_id,
                    plan_version=proposal.plan_version, node_id=proposal.node_id)

    async def observe(self, principal, binding):
        self.observations.append(binding.operation_id)
        proposal = next(p for p in self.proposals if p.plan_version == binding.plan_version and p.node_id == binding.node_id)
        return dict(**binding.model_dump(), status=self.outcome, parameters=proposal.inputs.model_dump(),
                    bank_ref='mock_bank_reference' if self.outcome == 'SUCCEEDED' else None)

    async def invalidate(self, principal, plan_id, version, operation_ids):
        if self.reject_invalidation:
            raise AgentError('UPSTREAM_UNAVAILABLE', 503)
        self.revoked.extend(operation_ids)
        return dict(plan_id=plan_id, plan_version=version, operation_ids=list(operation_ids), invalidated=True)


class ShapeTests(unittest.TestCase):
    def test_single_and_multi_dependency_topological_order(self):
        self.assertEqual(topological_order(GraphDefinition(nodes=[node('only')]).nodes), ['only'])
        graph = GraphDefinition(nodes=[node('c', deps=('a', 'b')), node('a'), node('b')])
        self.assertEqual(topological_order(graph.nodes), ['a', 'b', 'c'])

    def test_cycle_missing_dependency_duplicate_id_and_unknown_tool(self):
        for nodes in ([node('a', deps=('b',)), node('b', deps=('a',))], [node('a', deps=('absent',))],
                      [node('a'), node('a')], [node('a', tool='bank.submit_transfer')]):
            with self.subTest(nodes=nodes), self.assertRaises(ValidationError):
                GraphDefinition(nodes=nodes)

    def test_schema_rejects_permissions_state_bad_parameters_and_bad_preconditions(self):
        for draft in (node('a', risk_level='green'), node('a', status='SUCCEEDED'),
                      node('a', result_ref='forged'), node('a', inputs={'source_account_id': 'acc_daily', 'user_id': 'other'}),
                      node('a', 'execution.transfer', {**TRANSFER, 'amount_cents': True}),
                      node('a', preconditions=[{'node_id': 'other', 'field': 'available_cents', 'op': 'gte', 'value': 0}])):
            with self.subTest(draft=draft), self.assertRaises(ValidationError):
                GraphDefinition(nodes=[draft])


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = ContextStore()
        self.directory = FixtureDirectory(ROOT / 'data')
        self.session = self.store.create_session(P, 'personal_ledger', {'mandate_1'})
        self.tools, self.b = MockTools(), MockB()
        self.engine = DagEngine(self.store, self.directory, tools=self.tools, bank_execution=self.b,
                                clock=lambda: date(2026, 10, 29))

    def create(self, nodes=None):
        return self.engine.create(DagCreate(session_id=self.session.id, nodes=nodes or transfer_nodes()), P)

    async def run_plan(self, plan):
        return await self.engine.run(plan.plan_id, plan.plan_version, P)

    async def replan(self, plan, **updates):
        return await self.engine.replan(plan.plan_id, ReplanRequest(plan_version=plan.plan_version,
            reason='user_changed', updates=updates), P)

    def test_unauthorized_account_and_read_only_graph_rejected(self):
        with self.assertRaises(AgentError) as error:
            self.create([node('bad', inputs={'source_account_id': 'acc_other'})])
        self.assertEqual(error.exception.code, 'FORBIDDEN')
        self.session.constraints.read_only = True
        with self.assertRaises(AgentError) as error:
            self.create()
        self.assertEqual(error.exception.code, 'READ_ONLY')

    async def test_successful_single_read_and_result_provenance(self):
        result = await self.run_plan(self.create([node('a')]))
        self.assertEqual(result.nodes[0].status, 'SUCCEEDED')
        receipt = self.engine.result(result.plan_id, result.nodes[0].result_ref, P)
        self.assertEqual(receipt.source, 'mock_A')
        self.assertEqual(receipt.data.available_cents, 900000)

    async def test_failed_node_blocks_descendants_but_independent_nodes_run(self):
        self.tools.fail.add('bank.quote')
        result = await self.run_plan(self.create(transfer_nodes() + [node('independent')]))
        states = {n.node_id: n.status for n in result.nodes}
        self.assertEqual(states['quote'], 'FAILED')
        self.assertEqual(states['payment'], 'BLOCKED')
        self.assertEqual(states['independent'], 'SUCCEEDED')

    async def test_untrusted_output_cannot_change_permissions_or_risk(self):
        self.tools.inject = True
        result = await self.run_plan(self.create())
        self.assertEqual(result.nodes[0].status, 'FAILED')
        self.assertEqual(result.nodes[0].error_code, 'INVALID_TOOL_RESULT')
        self.assertEqual(result.nodes[-1].risk_level, 'B_REQUIRED')
        self.assertEqual(self.b.proposals, [])

    async def test_replanning_only_resets_changed_nodes_and_downstream(self):
        first = await self.run_plan(self.create())
        second = await self.replan(first, amount_cents=3000)
        self.assertEqual(second.plan_version, 2)
        states = {n.node_id: n for n in second.nodes}
        self.assertEqual(states['balance'].result_ref, first.nodes[0].result_ref)
        self.assertEqual(states['recipient'].result_ref, first.nodes[1].result_ref)
        self.assertEqual(states['quote'].status, 'PENDING')
        self.assertEqual(states['payment'].inputs.amount_cents, 3000)
        self.assertEqual(set(second.diff.affected_nodes), {'quote', 'payment'})
        self.assertEqual(second.diff.reason, 'user_changed')
        with self.assertRaises(AgentError) as error:
            await self.run_plan(first)
        self.assertEqual(error.exception.code, 'PLAN_STALE')

    async def test_recipient_and_payment_date_changes_create_versions(self):
        first = self.create()
        second = await self.replan(first, recipient_id='rec_3')
        third = await self.replan(second, execution_date='2026-11-03')
        self.assertEqual(third.plan_version, 3)
        self.assertEqual(third.nodes[-1].inputs.recipient_id, 'rec_3')
        self.assertEqual(third.nodes[-1].inputs.execution_date, '2026-11-03')
        self.assertEqual(self.engine.get(first.plan_id, P, 1).state, 'SUPERSEDED')

    async def test_salary_date_only_recomputes_cashflow(self):
        nodes = [node('balance'), node('forecast', 'cashflow.simulate', dict(source_account_id='acc_daily',
            amount_cents=600000, target_date='2026-12-01', salary_date='2026-11-12'), ('balance',))]
        first = await self.run_plan(self.create(nodes))
        second = await self.replan(first, salary_date='2026-11-17')
        self.assertEqual(second.diff.affected_nodes, ['forecast'])
        self.assertEqual(second.nodes[0].result_ref, first.nodes[0].result_ref)

    async def test_valid_unexpected_upstream_result_automatically_versions_plan(self):
        nodes = transfer_nodes()
        nodes[0]['expected_result'] = [{'field': 'available_cents', 'value': 900000}]
        self.tools.balance = 800000
        result = await self.run_plan(self.create(nodes))
        self.assertEqual(result.plan_version, 2)
        self.assertEqual(result.diff.reason, 'upstream_changed')
        self.assertEqual(result.diff.result_changes[0].expected, 900000)
        self.assertEqual(result.diff.result_changes[0].actual, 800000)
        self.assertEqual(result.nodes[0].status, 'SUCCEEDED')
        self.assertEqual(result.nodes[2].status, 'PENDING')
        self.assertEqual([c[0] for c in self.tools.calls], ['bank.balance'])

    async def test_successful_mock_financial_node_is_never_reexecuted_or_rewritten(self):
        first = await self.run_plan(self.create())
        self.assertEqual(first.nodes[-1].status, 'WAITING_B')
        prepared = await self.engine.prepare(first.plan_id, 1, 'payment', P)
        first = await self.run_plan(first)
        self.assertEqual(first.nodes[-1].status, 'SUCCEEDED')
        second = await self.replan(first, amount_cents=5000)
        self.assertEqual(second.nodes[-1].inputs.amount_cents, 2500)
        self.assertEqual(second.nodes[-1].result_ref, first.nodes[-1].result_ref)
        self.assertIn('COMPLETED_ACTION_IMMUTABLE:payment', second.diff.conflicts)
        await self.run_plan(second)
        self.assertEqual(self.b.observations, [prepared.operation_id])
        with self.assertRaises(AgentError):
            await self.engine.prepare(second.plan_id, 2, 'payment', P)
        self.assertEqual(len(self.b.proposals), 1)
        self.assertEqual(self.engine.result(second.plan_id, first.nodes[-1].result_ref, P).source, 'mock_B')

    async def test_new_version_invalidates_old_proposals_and_never_copies_credentials(self):
        first = await self.run_plan(self.create())
        operation = await self.engine.prepare(first.plan_id, 1, 'payment', P)
        same = await self.engine.prepare(first.plan_id, 1, 'payment', P)
        self.assertEqual(same, operation)
        second = await self.replan(first, amount_cents=3000)
        self.assertEqual(self.b.revoked, [operation.operation_id])
        self.assertNotIn('authorization', second.model_dump_json())
        self.assertIsNone(second.nodes[-1].operation_id)
        with self.assertRaises(AgentError) as error:
            self.engine.guard_legacy_operation(P, operation.operation_id)
        self.assertEqual(error.exception.code, 'PLAN_STALE')

    async def test_failed_invalidation_does_not_publish_new_version(self):
        first = await self.run_plan(self.create())
        await self.engine.prepare(first.plan_id, 1, 'payment', P)
        self.b.reject_invalidation = True
        with self.assertRaises(AgentError):
            await self.replan(first, amount_cents=3000)
        self.assertEqual(self.engine.get(first.plan_id, P).plan_version, 1)

    async def test_false_invalidation_receipt_is_rejected(self):
        first = await self.run_plan(self.create())
        await self.engine.prepare(first.plan_id, 1, 'payment', P)
        async def false_receipt(principal, plan_id, version, operation_ids):
            return dict(plan_id=plan_id, plan_version=version, operation_ids=operation_ids, invalidated=False)
        self.b.invalidate = false_receipt
        with self.assertRaises(AgentError) as error:
            await self.replan(first, amount_cents=3000)
        self.assertEqual(error.exception.code, 'UPSTREAM_UNAVAILABLE')
        self.assertEqual(self.engine.get(first.plan_id, P).plan_version, 1)

    async def test_pending_unknown_blocks_replan_and_duplicate_proposal(self):
        first = await self.run_plan(self.create())
        await self.engine.prepare(first.plan_id, 1, 'payment', P)
        self.b.outcome = 'PENDING_UNKNOWN'
        first = await self.run_plan(first)
        self.assertEqual(first.nodes[-1].status, 'PENDING_UNKNOWN')
        with self.assertRaises(AgentError) as error:
            await self.replan(first, amount_cents=3000)
        self.assertEqual(error.exception.code, 'PENDING_UNKNOWN')
        self.assertEqual(len(self.b.proposals), 1)

    async def test_precondition_failure_and_scope_revocation(self):
        nodes = transfer_nodes()
        nodes[2]['preconditions'] = [{'node_id': 'balance', 'field': 'available_cents', 'op': 'gte', 'value': 9999999}]
        result = await self.run_plan(self.create(nodes))
        self.assertEqual(result.nodes[2].status, 'BLOCKED')
        with self.assertRaises(AgentError):
            await self.engine.run(result.plan_id, 1, OTHER)
        with self.assertRaises(AgentError):
            await self.engine.run(result.plan_id, 1, Principal(P.subject, (), P.dataset_ids))

    async def test_concurrent_replans_have_one_winner(self):
        first = self.create()
        outcomes = await asyncio.gather(self.replan(first, amount_cents=3000),
                                        self.replan(first, amount_cents=4000), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, AgentError) for x in outcomes), 1)
        self.assertEqual(self.engine.get(first.plan_id, P).plan_version, 2)

    async def test_unknown_prepare_survives_run_and_never_repeats_remote_prepare(self):
        async def timeout(principal, proposal):
            self.b.proposals.append(proposal)
            raise httpx.ReadTimeout('response lost')
        self.b.prepare = timeout
        first = await self.run_plan(self.create())
        with self.assertRaises(AgentError):
            await self.engine.prepare(first.plan_id, 1, 'payment', P)
        ran = await self.run_plan(first)
        self.assertEqual(ran.nodes[-1].status, 'PENDING_UNKNOWN')
        with self.assertRaises(AgentError):
            await self.engine.prepare(first.plan_id, 1, 'payment', P)
        self.assertEqual(len(self.b.proposals), 1)

    async def test_constraints_rechecked_after_waiting_for_session_lock(self):
        first = await self.run_plan(self.create())
        await self.session.lock.acquire()
        task = asyncio.create_task(self.engine.prepare(first.plan_id, 1, 'payment', P))
        await asyncio.sleep(0)
        self.session.constraints.read_only = True
        self.session.lock.release()
        with self.assertRaises(AgentError) as error:
            await task
        self.assertEqual(error.exception.code, 'PLAN_STALE')
        self.assertEqual(self.b.proposals, [])

    async def test_replan_required_state_can_reconcile_pending_bank_outcome(self):
        nodes = transfer_nodes() + [node('second', 'execution.transfer', TRANSFER, ('quote',)),
            node('zcheck', deps=('second',), expected_result=[{'field': 'available_cents', 'value': 1}])]
        first = await self.run_plan(self.create(nodes))
        await self.engine.prepare(first.plan_id, 1, 'payment', P)
        await self.engine.prepare(first.plan_id, 1, 'second', P)
        observe = self.b.observe
        async def mixed(principal, binding):
            self.b.outcome = 'PENDING_UNKNOWN' if binding.node_id == 'payment' else 'SUCCEEDED'
            return await observe(principal, binding)
        self.b.observe = mixed
        try:
            await self.run_plan(first)
        except AgentError:
            pass
        self.assertEqual(self.engine.get(first.plan_id, P).state, 'REQUIRES_REPLAN')
        self.b.observe = observe
        self.b.outcome = 'SUCCEEDED'
        resolved = await self.engine.reconcile(first.plan_id, 1, P)
        self.assertEqual(resolved.plan_version, 2)
        self.assertTrue(all(n.status == 'SUCCEEDED' for n in resolved.nodes if n.tool_name == 'execution.transfer'))

    async def test_bad_bank_parameters_and_false_invalidation_are_not_authority(self):
        first = await self.run_plan(self.create())
        await self.engine.prepare(first.plan_id, 1, 'payment', P)
        observe = self.b.observe
        async def forged(principal, binding):
            raw = await observe(principal, binding)
            raw['parameters']['amount_cents'] = 9999
            return raw
        self.b.observe = forged
        ran = await self.run_plan(first)
        self.assertEqual(ran.nodes[-1].status, 'PENDING_UNKNOWN')
        self.assertEqual(ran.nodes[-1].error_code, 'INVALID_B_RESULT')

    async def test_run_and_replan_are_serialized(self):
        entered, release = asyncio.Event(), asyncio.Event()
        read = self.tools.read
        async def delayed(*args):
            entered.set()
            await release.wait()
            return await read(*args)
        self.tools.read = delayed
        first = self.create()
        run = asyncio.create_task(self.run_plan(first))
        await entered.wait()
        replan = asyncio.create_task(self.replan(first, amount_cents=3000))
        await asyncio.sleep(0)
        self.assertFalse(replan.done())
        release.set()
        self.assertEqual((await run).plan_version, 1)
        self.assertEqual((await replan).plan_version, 2)
        count = len(self.tools.calls)
        with self.assertRaises(AgentError):
            await self.run_plan(first)
        self.assertEqual(len(self.tools.calls), count)

    async def test_preparation_recovery_queries_b_instead_of_recreating(self):
        prepare = self.b.prepare
        async def lost_reply(principal, proposal):
            await prepare(principal, proposal)
            raise httpx.ReadTimeout('response lost')
        self.b.prepare = lost_reply
        first = await self.run_plan(self.create())
        with self.assertRaises(AgentError):
            await self.engine.prepare(first.plan_id, 1, 'payment', P)
        async def lookup(principal, proposal):
            return dict(operation_id='mock_op_1', plan_id=proposal.plan_id, plan_version=proposal.plan_version,
                        node_id=proposal.node_id)
        self.b.lookup = lookup
        result = await self.engine.reconcile(first.plan_id, 1, P)
        self.assertEqual(result.nodes[-1].status, 'SUCCEEDED')
        self.assertEqual(len(self.b.proposals), 1)


if __name__ == '__main__':
    unittest.main()
