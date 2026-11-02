# Detailed checklist implementation plan

Goal: address the ten findings in /workspace/futureledger-document-audit/report.md against the user-supplied 92-item checklist, retain honest capability boundaries and create a reviewable GitHub PR.

Design: Demo metrics include directional transfers in outflow/inflow; formal C static contracts and published gold remain separate. Security guards run before shortcuts or providers. Pending query context is session scoped and joined before routing. Chat planning extracts closed parameters and calls deterministic synthetic simulation with scoped starting balances and explicit fixed-event assumptions. Vue renders typed scenario cards and correct units/labels. No bank write is authorized by chat planning.

- [x] Ledger/semantic: regression tests for transfer sums, directions and new incoming/average metrics; preserve independent oracle.
- [x] API safety/context: reject credentials/unsafe or foreign scopes before model/shortcut; preserve dated followups; localized CSV and display metadata; idempotent completed confirmation.
- [x] Parser/planning: full-month ranges, merchant groups, latest details, missing parameter clarification; deterministic scoped chat planning and comparison.
- [x] Vue: correct grouped units, labels, hidden IDs; typed planning cards and curves.
- [x] Verification: root suite/C4, frontend tests/build, real Chromium detailed checklist smoke, independent review.
- [ ] Delivery: document boundaries, commit and push authorized fork branch; attempt PR to Maggie-maoyiqi/FutureLedger:main, report any API permission blocker.

Execution: independent file ownership under dispatching-parallel-agents; coordinator owns demo/api.py, store type additions and integration tests. All defects get failing regressions before fixes. No production secrets are requested or committed. No unrequested merge or publish.
