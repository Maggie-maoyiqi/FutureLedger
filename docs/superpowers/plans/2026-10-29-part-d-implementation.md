# Part D Analytics and Cashflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete Part D for the FutureLedger demo by hardening deterministic analytics, adding an independent production cash-flow engine, exposing simulation through FastAPI, and proving the result against the 180 analysis and 30 cash-flow cases.

**Architecture:** Preserve the existing `demo/semantic.py` and `demo/query.py` contract for bill analysis, while adding a pure `demo/cashflow.py` module for daily integer-cent simulation. `demo/api.py` will expose a session-scoped simulation endpoint and map validation failures to structured HTTP errors. Benchmark tests will compare production modules to the checked-in golden cases; `scripts/reference_engine.py` remains an oracle only.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, SQLite fixture, unittest, JSON benchmark cases.

**Spec:** `docs/superpowers/specs/2026-10-29-part-d-design.md`

## Global Constraints

- Money stays in integer cents throughout computation and API responses.
- Analysis only reads POSTED facts and never accepts model-supplied `user_id` or raw SQL.
- Refunds are counted by refund posting date; net expense is gross expense minus refunds.
- Cash flow uses a 30-day daily balance series, a protected floor of 300000 cents, and explicit salary/purchase events.
- Production code must not import `scripts/reference_engine.py`.
- Existing query, transfer, and frontend-facing demo behavior must remain compatible.

## Review Focus

- A duplicate import with the same `(account_id, external_id)` must count once, while different external IDs with the same amount/date remain separate.
- A Shanghai local-month boundary must include `2026-09-01T00:00:00+08:00` and exclude the prior local day.
- Failed and pending transactions must not appear in completed-spending calculations.
- Changing salary day or purchase day must create a new deterministic cash-flow result and report the first constraint conflict.
- An unauthenticated or cross-user simulation request must not disclose account data or produce a result.

### Task 1: Add production cash-flow primitives

**Files:**
- Create: `demo/cashflow.py`
- Test: `tests/test_part_d.py`

**Interfaces:**
- Consumes: the scenario fields in `data/demo_scenario.json` and the `salary_day`, `purchase_day`, and `daily_budget_cents` values used by `benchmark/cashflow_cases.json`.
- Produces: `simulate_cashflow(*, salary_day: int, purchase_day: int, daily_budget_cents: int = 8000, opening_balance_cents: int = 900000, floor_cents: int = 300000, goal_cents: int = 600000, days: int = 30) -> CashflowResult`; `CashflowResult` serializes to `points_cents`, `min_balance_cents`, `min_day`, `feasible`, `conflicts`, `assumptions`, and `events`.

- [ ] **Step 1: Write the failing tests**
  - `test_cashflow_matches_baseline_anchor`: day 13 purchase with salary day 12 yields minimum `452200` on day 11 and final `500200`.
  - `test_cashflow_delayed_salary_is_infeasible`: salary day 17 and purchase day 13 yields `-187800` on day 16 and `feasible=False`.
  - `test_cashflow_replan_after_delayed_salary`: salary day 17 and purchase day 19 yields `412200` on day 16 and `feasible=True`.
  - `test_cashflow_rejects_invalid_days_and_budget`: invalid days, negative budget, and non-integer values raise `ValueError`.

- [ ] **Step 2: Run the focused tests to verify they fail**

  Run: `python -m unittest tests.test_part_d.CashflowCoreTests -v`

  Expected: import or attribute failures because `demo.cashflow` does not exist yet.

- [ ] **Step 3: Implement the smallest pure simulator**

  In `demo/cashflow.py`, define frozen dataclasses or Pydantic models for the result and event records. Apply fixed events in day order, subtract the daily budget each day, apply the salary and purchase event on their selected days, compute the minimum balance and first minimum day, and add a conflict record when the floor is breached. Validate `1 <= salary_day <= days`, `1 <= purchase_day <= days`, `daily_budget_cents >= 0`, and positive `days`.

- [ ] **Step 4: Run the focused tests to verify they pass**

  Run: `python -m unittest tests.test_part_d.CashflowCoreTests -v`

  Expected: all four tests pass.

### Task 2: Harden analytics semantics and evidence

**Files:**
- Modify: `demo/semantic.py`
- Modify: `demo/query.py`
- Test: `tests/test_part_d.py`

**Interfaces:**
- Consumes: `IntentPlan`, `Ledger.execute`, `data/benchmark.sqlite`.
- Produces: existing query result shape with deterministic rows and sorted `evidence_ids`; no API caller changes.

- [ ] **Step 1: Write failing tests for Part D metric boundaries**
  - `test_transfer_is_not_consumption`: outgoing transfer does not increase gross/net expense.
  - `test_income_excludes_refund_and_internal_transfer`: income only counts POSTED income/IN.
  - `test_failed_and_pending_are_excluded`: failed and pending rows do not contribute to value or evidence.
  - `test_evidence_is_sorted_and_scope_isolated`: evidence IDs are deterministic and disjoint between users.
  - `test_empty_analysis_returns_zero`: an empty date range returns a zero value and an empty evidence list.

- [ ] **Step 2: Run the focused analytics tests to verify the expected failures**

  Run: `python -m unittest tests.test_part_d.AnalyticsBoundaryTests -v`

  Expected: failures on the current transfer/income/status semantics where the existing implementation does not match the Part D contract.

- [ ] **Step 3: Implement minimal semantic fixes**

  Align metric expressions and `KINDS` with `docs/03-数据库与接口契约.md`: gross expense is POSTED `expense/OUT`, refund is POSTED `refund/IN`, net subtracts refunds, income is POSTED `income/IN`, and completed transaction counts exclude failed/pending facts. Preserve explicit transfer metrics for transfer-specific queries. Keep account and user filters parameterized and continue rejecting unknown merchant/category filters.

- [ ] **Step 4: Run focused and existing analysis tests**

  Run: `python -m unittest tests.test_part_d.AnalyticsBoundaryTests tests.test_reference.AnalysisBoundary -v`

  Expected: all Part D boundary tests and existing reference boundary tests pass.

### Task 3: Add the simulation API boundary

**Files:**
- Modify: `demo/api.py`
- Modify: `demo/planner.py` only if the planning route needs a shared plan identifier or response wording.
- Test: `tests/test_part_d.py`

**Interfaces:**
- Consumes: authenticated `session_id`, a validated simulation request, and `demo.cashflow.simulate_cashflow`.
- Produces: `POST /api/plans/{plan_id}/simulate` returning `{status, plan_id, plan_version, result}`; `result` contains the full daily series, minimum balance, feasibility, assumptions, events, and conflicts.

- [ ] **Step 1: Write failing API tests**
  - `test_simulation_requires_valid_session`: invalid session returns 403.
  - `test_simulation_returns_anchor_result`: an authenticated request for salary day 17/purchase day 19 returns the expected feasible result.
  - `test_simulation_rejects_extra_fields_and_invalid_values`: Pydantic extra fields and invalid days return 422 with a stable error code.
  - `test_simulation_does_not_write_ledger`: the SQLite transaction count and balances remain unchanged after simulation.

- [ ] **Step 2: Run the API tests to verify they fail**

  Run: `python -m unittest tests.test_part_d.SimulationApiTests -v`

  Expected: 404 for the missing route or missing response fields.

- [ ] **Step 3: Implement the endpoint**

  Add a strict request model with `plan_version`, `salary_day`, `purchase_day`, optional `daily_budget_cents`, and optional `floor_cents`. Resolve the user from the existing session map, validate the request before invoking the pure simulator, and return a deterministic result without touching `Ledger` write methods. Use HTTP 403 for invalid sessions and HTTP 422 for validation errors.

- [ ] **Step 4: Run the API tests to verify they pass**

  Run: `python -m unittest tests.test_part_d.SimulationApiTests -v`

  Expected: all four API tests pass.

### Task 4: Prove production-path benchmark agreement

**Files:**
- Modify: `tests/test_part_d.py`
- Modify: `benchmark/VERIFICATION.md`
- Modify: `scripts/run_benchmark.py` only if a production-mode comparison flag is needed.

**Interfaces:**
- Consumes: `Ledger.execute`, `simulate_cashflow`, `benchmark/analysis_cases.json`, and `benchmark/cashflow_cases.json`.
- Produces: a local verification report that distinguishes production-path results from reference-oracle results.

- [ ] **Step 1: Write the benchmark tests**
  - `test_all_analysis_cases_match_production_ledger`: all 180 expected values and evidence IDs match `Ledger.execute`.
  - `test_all_cashflow_cases_match_production_simulator`: all 30 expected series and summary fields match `simulate_cashflow`.
  - `test_production_modules_do_not_import_reference_engine`: inspect module imports and fail if Part D modules import the oracle.

- [ ] **Step 2: Run benchmark tests to verify they fail where production behavior is missing**

  Run: `python -m unittest tests.test_part_d.BenchmarkAgreementTests -v`

  Expected: cash-flow cases fail before the simulator exists; analysis failures identify any semantic changes that need compatibility handling.

- [ ] **Step 3: Implement the production comparison harness**

  Keep the oracle call inside the test/benchmark comparison only. Do not import it from `demo/cashflow.py`, `demo/query.py`, or `demo/api.py`. Write the verification note with separate counts for production results and oracle results.

- [ ] **Step 4: Run the complete suite and benchmark**

  Run:

  ```powershell
  $env:PYTHONUTF8='1'
  python scripts/generate_data.py
  python scripts/generate_scenarios.py
  python -m unittest discover -s tests -v
  python scripts/run_benchmark.py
  ```

  Expected: the full suite passes, the benchmark reports 210 production-path cases passing, and no Part D case is marked passed merely because the oracle ran.

### Task 5: Final documentation and commit readiness

**Files:**
- Modify: `benchmark/VERIFICATION.md`
- Modify: `README.md` only to document the new simulation endpoint and local verification command.

**Interfaces:**
- Consumes: passing tests and benchmark output from Tasks 1–4.
- Produces: a reviewer-readable Part D summary and a clean `codex/analytics-cashflow` branch ready for commit.

- [ ] **Step 1: Record the implemented behavior**

  Document the endpoint request/response, the cash-flow assumptions, the 180/30 production counts, and any known limits. Keep reference-oracle results in a separate section.

- [ ] **Step 2: Run repository checks**

  Run the full test suite, benchmark, and `node scripts/check_prototype.cjs`. Inspect `git diff --check` and `git status --short`.

- [ ] **Step 3: Commit the completed Part D changes**

  ```powershell
  git add demo tests benchmark docs README.md
  git commit -m "feat: complete Part D analytics and cashflow"
  ```

  Do not push or create a GitHub pull request unless separately requested.
