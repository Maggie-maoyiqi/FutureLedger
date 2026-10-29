# Part D Analytics and Cashflow Design

## Intent

Implement the Part D deliverable for the existing FutureLedger demo: trusted,
deterministic bill analysis and cash-flow simulation that can be consumed by
the Agent and the frontend. Financial values must come from validated plans
and canonical ledger data, not model text or the reference oracle.

## Current state

The repository already has:

- `demo/semantic.py` with the published `IntentPlan` and metric catalog.
- `demo/query.py` with read-only SQLite queries, grouping, details, and
  evidence IDs.
- `scripts/reference_engine.py` with the offline analysis and cash-flow
  oracle.
- 180 analysis cases and 30 cash-flow cases under `benchmark/`.

The cash-flow oracle is not wired into the demo API. The existing analysis
implementation also predates the expanded Part D requirements, so this work
will strengthen the deterministic service and preserve the current demo
behavior.

## Goals

1. Keep the current analysis routes working while enforcing the documented
   POSTED/status, refund, transfer, account-scope, duplicate, and evidence
   rules.
2. Add a production cash-flow module that simulates daily integer-cent
   balances, compares salary and purchase scenarios, applies hard and soft
   constraints, and explains infeasible plans.
3. Expose cash-flow simulation through a stable FastAPI endpoint that returns
   daily points, minimum balance, feasibility, assumptions, and conflicts.
4. Verify the production paths against all 180 analysis and 30 cash-flow
   benchmark cases without importing `scripts/reference_engine.py`.
5. Keep model-facing inputs structured and forbid raw SQL, user-supplied
   identity, or bank writes in Part D.

## Non-goals

- Bank execution, transfer authorization, worker recovery, or PostgreSQL
  deployment.
- Replacing the existing Agent planner or frontend.
- Free-form Text2SQL beyond the existing structured-plan contract.
- Treating the reference engine as production code.

## Approaches considered

### Complete integrated route (selected)

Keep the current query service, add a focused cash-flow module, add an API
endpoint, and add production-path benchmark tests. This matches the team
document, gives the frontend a usable contract, and preserves the existing
demo.

### Minimal cash-flow-only route

Add only the simulator and tests while leaving analytics untouched. This is
faster, but it leaves Part D's expanded metric and evidence requirements
unverified and does not provide the integration boundary needed by E.

### Standalone calculation library

Build independent analysis and cash-flow functions without FastAPI integration.
This is easy to test but would leave C and E without a production interface.

## Design

### Analytics

Use the existing `IntentPlan` validation and `Ledger` read-only connection.
Keep SQL construction allow-listed and parameterized. The analytics result
will contain `value_cents` or grouped/detail rows, sorted `evidence_ids`, and
the query assumptions needed by the UI. Analysis remains read-only and scoped
to the authenticated user's accounts.

Metric semantics follow `docs/03-数据库与接口契约.md`: gross expense is
POSTED expense/OUT, refunds are POSTED refund/IN counted by posting date,
net expense subtracts refunds, income excludes refunds and internal transfers,
and failed/pending facts do not count as completed spending.

### Cash flow

Add a small pure module under `demo/cashflow.py` with typed input events and
results. The simulator applies events and daily budget in day order using
integer cents. It returns the full 30-day balance series, minimum balance and
day, feasibility against the protection floor, event source labels, and
conflict reasons. A scenario helper will evaluate salary-day and purchase-day
candidates using the existing `data/demo_scenario.json` assumptions.

### API

Add a request model and `POST /api/plans/{id}/simulate` in `demo/api.py`.
The demo endpoint will accept a plan version plus salary/purchase assumptions,
use the current authenticated session, and return the deterministic cash-flow
result. Invalid days, negative budgets, unknown sessions, and stale/malformed
plans return structured errors rather than partial results.

### Verification

Add focused tests first for validation, metric edge cases, cash-flow anchors,
scenario conflicts, and API responses. Then run the full existing suite and
the 180/30 benchmark comparison. The reference engine is called only by the
comparison test or benchmark runner, never by production modules.

## Files expected to change

- `demo/cashflow.py` — new pure cash-flow simulator and scenario evaluator.
- `demo/api.py` — simulation request model, route, and response mapping.
- `demo/query.py` / `demo/semantic.py` — only where required to close the
  documented metric and evidence gaps without breaking existing behavior.
- `tests/test_part_d.py` — Part D unit and API tests.
- `tests/test_reference.py` — only if the Windows-safe fixture encoding needs
  a small portability fix.
- `benchmark/VERIFICATION.md` — record production-path results separately
  from reference-oracle results.

## Acceptance criteria

- Existing demo query and transfer tests continue to pass.
- Cash-flow anchors match the published values, including the salary-delay
  replan from day 13 to day 19.
- No Part D production module imports `scripts/reference_engine.py`.
- All benchmark cases have explicit production results; unrun cases are not
  reported as passing.
- API output is deterministic, account-scoped, integer-cent based, and
  sufficient for the frontend to render a chart and a daily table.
