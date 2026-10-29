# C4: harden Agent integration boundaries and add reproducible offline acceptance

## Summary

Validate existing C1–C3 on the current Cloud checkout and fix C-owned boundary defects. Status **PARTIAL**: implemented-scope offline checks pass, while formal A/B/D integration, broader analysis/public mandate scope flow and durable recovery remain incomplete.

Stacked base: `codex/agent-orchestration` / `137e66f`. Branch: `codex/agent-final-acceptance`. Do not merge into main automatically.

## Changes

- Bind legacy B commands to subject/plan/version, serialize per operation, latch submission attempts and preserve successful facts. Lost/unknown outcomes only query B; no automatic money retry.
- Add optional in-process Agent chat replay protection with current authorization checks and changed-body rejection.
- Revalidate typed provider/tool responses without leaking serializer diagnostics; reject recognizable credentials before model calls.
- Enforce transaction dates/limit and cashflow feasibility consistency; normalize fixture business dates to Shanghai; report partial success accurately.
- Add C4 unit, integration, security, 16-fault probes, contract/schema tests, safe CI, fixture/quality/benchmark runners and OpenAPI documentation.

## Tests

Tested clean code SHA `726d3e65adc050342319074484e98ad613f0c23d` on Python `3.12.14`. All 12 local commands exit 0.

- Full suite **189/189** (C unit6 + integration12 + security10 + fault16 + original regression145), fail0/skip0.
- Local Agent natural-language analysis **180/180**, no gold input to planner; C1_fixture, not real LLM/formal D.
- Cashflow **30/30 Mock oracle-backed wiring**, not numerical engine validation.
- Reference baseline **210/210**; reference self-checks **12/12** (also included in original regression).
- `services/api` statement coverage **91.29%**, branch coverage **77.22%**.
- Two independent processes demonstrate state LOSS and fail-closed old IDs, not recovery.
- Schema drift, actual response validation, limited undefined-name lint, pip check, prototype and diff checks pass.

Reproduce:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-c4.txt
FUTURELEDGER_PROVIDER=local .venv/bin/python scripts/run_c4_quality.py --output /tmp/futureledger-c4-evidence
```

Requires Python3.12 and Node24, synthetic data only; no LLM key or payment service. Remote GitHub Actions NOT_RUN; workflow is added, not represented as a green remote run.

## Security Notes

C never owns B risk/confirmation/verification/quotas or bank writes. Prompt/tool data cannot add authority or SQL capabilities. Submission-lost cases retain UNKNOWN and prevent repeat forwarding. Credential-pattern rejection is not general DLP. Developer auth and in-memory guards are not production identity/idempotency/persistence. All credentials in tests are synthetic.

## Integration Dependencies

A seven-read contracts: MOCK, REAL BLOCKED. B explicit command/state contracts: MOCK, REAL BLOCKED. D: C1 fixture and MOCK only; formal AnalysisPlan/Cashflow integration BLOCKED. E receives additive C4 OpenAPI, examples, states and SSE limitations; full frontend/sandbox NOT_RUN. Existing C1/C2/C3 snapshots and published gold data unchanged.

## Known Risks

Formal schema still lacks income/ranking/trends; public mandate scope authorization continuation is incomplete. Memory sessions/events/replay do not survive restarts or coordinate multiple workers. No production session TTL or durable SSE epoch. Optional chat replay requires clients to reuse the header; B must supply durable idempotency and authoritative recovery. Acceptance60 retains source NOT_RUN, overlay50 BLOCKED/10 NOT_RUN. No real bank/LLM/production D success is claimed.

Full file list, findings, commands/times/exits: `docs/c4-final-report.md`, `docs/c4-results.json`. Architecture/API examples: `docs/c4-integration.md`. No remote PR was created or automatically merged.
