# FutureLedger Text2SQL demo plan

## Goal and scope

Build a locally runnable personal-ledger demo on the repository's synthetic SQLite fixture. Query requests remain read-only. A separate execution route supports only a synthetic transfer between the two demo users, after an explicit preview, user confirmation, and FutureLedger password reauthentication; it writes to a runtime copy of the fixture, never to a bank or the canonical benchmark database. The runtime copy isolates editable demo state from the canonical benchmark fixture and makes repeated demonstrations reproducible. A real deployment must write through the authoritative ledger/bank adapter, not clone the ledger. A future PostgreSQL deployment needs production authentication, role/RLS configuration, and audited database migrations.

## Architecture

Chat-style browser UI → FutureLedger login/session boundary → LLM semantic router. The router sees the current question and selects query, execution, planning, view, clarification or blocked; it receives no ledger rows. Query requests then make a second LLM call to produce a closed JSON IntentPlan → strict validation and clarification → deterministic SQL compiler → read-only scoped SQLite execution → result card, chart or table, and CSV export. Only this second call receives the current user's account IDs, merchant names and up to 30 recent posted transaction examples. Planning requests are a distinct route: LLM parses a bounded goal/constraint object; the backend fetches only the current user's authorized balances, posted income/expenses, and relevant recurring commitments using server-owned parameterized SQL; a deterministic daily cashflow simulator computes scenarios, reserve breaches, and target dates. The LLM may explain those scenario outputs, but does not compute amounts, receive the full ledger, or trigger a transfer. If a required constraint or account scope is ambiguous, ask the user before retrieving additional data. Execution requests bypass Text2SQL and enter preview → password authorization → purpose-category selection → explicit final confirmation → atomic two-user synthetic-ledger update. Outgoing transfers count as outflows and incoming transfers count as inflows; sender-selected purpose is stored on the outgoing row. Unrelated questions return a view-only text message. The provider is an OpenAI-compatible adapter with configurable base URL, model and environment-held API key. The compiler alone emits query SQL from fixed expressions and identifiers.

This follows nanobot's separation between channel/session orchestration and provider-facing model execution, without importing nanobot's general file, shell, web or payment tools into a banking query surface.

## API contracts

- `GET /api/health`: demo readiness and provider mode, no secret values.
- `GET /api/semantic`: published metrics/dimensions/categories for the fixed dataset.
- `POST /api/auth/register` and `/api/auth/login`: one-time password setup and login for the two synthetic demo users. SQLite stores a salted scrypt hash, never a plaintext password. The token is in-memory and is cleared on logout or server restart; this is a local demo login, not production authentication.
- `POST /api/sessions`: authenticated local demo session bound to the logged-in fixture user and `personal_ledger` semantic version.
- `POST /api/ask`: question plus session ID; returns a concise answer, result rows and presentation type, or a clarification/blocked/unsupported message. IntentPlan, SQL, evidence IDs and internal account scope stay server-side. No raw model SQL is accepted.
- Transfer intents return a preview without changing balances. The user enters their FutureLedger password in a separate form; `POST /api/actions/{id}/authorize` verifies it and returns a short-lived authorization token. The user then selects an outgoing purpose category and confirms; `POST /api/actions/{id}/confirm` consumes the token and atomically updates both synthetic users' balances and paired transaction rows. The sender row uses the selected category and counts toward spending; the recipient row counts toward income. Passwords never enter chat history, IntentPlan, or the model. The action ID is tied to its originating demo session.
- The browser keeps a left-side local conversation list and a right-side chat stream. A single aggregate becomes a number card; normal grouping becomes a table; ranking can use bars; month trends use lines with visible y-axis values, and category or merchant series use separate colored lines. IntentPlan, SQL, evidence IDs, and internal scope are not returned in the answer payload.
- The router receives no transaction context. Only query-routed requests call the second, query-planning model request. It receives the question, public semantic catalog, current user's account IDs and merchant names, plus up to 30 recent posted transaction examples (date, category, merchant, type, direction, amount). It never receives another user's records, free-text memo, account balance, or full transaction history. Query totals and detail rows are computed by SQLite under the server-derived user/account scope; query results are returned to the browser and not sent back to the model. This bounded sample policy is for the synthetic local demo; production needs an explicit, revocable data-sharing choice and provider retention review.
- `GET /api/queries/{id}/export.csv`: CSV for a completed query, scoped to the same demo session.

## Initial query contract

Metrics: total outflow (expense/OUT plus transfer/OUT), refund, net outflow (including transfers less refunds), total inflow (income/IN plus transfer/IN), transaction count, average outflow, transfer amount and transfer count. Optional filters: category, merchant and account. A half-open local date range is required. Grouping: none, category, merchant, month or account; monthly trends can also add category, merchant or account as a second series dimension. A scalar is rendered as a number, ordinary groupings as a table, explicit rankings/comparisons as bars, and time series as lines with left-side tick labels.

## Foundation and feature slices

1. Add FastAPI health/session API, configuration, fixture initialization, minimal web page, and a smoke test.
2. Add semantic contract, route classifier and strict intent parsing. Unknown enumerations or missing ranking metric return clarification.
3. Add parameterized compiler and read-only executor with server-derived user/account scope; test exact aggregates, rankings, monthly trends, details and isolation.
4. Add DeepSeek/OpenAI-compatible provider, bounded JSON output, timeouts and errors; mocked provider tests exercise the same pipeline.
5. Add result rendering and CSV export; test empty data, malicious text, provider failure, and startup with no key.

## Benchmark and verification

Use existing `benchmark/analysis_cases.json` as the first 180 scalar cases; add focused tests for new intents and policy decisions. Tests compare values and evidence IDs, reject injected user/SQL fields, and verify no write statement or cross-user row can reach the executor. Validate API startup and a browser-visible query on the generated fixture. No live provider call is required for deterministic tests.

## Risks and limits

The repo currently has only synthetic SQLite data; PostgreSQL schema and production authentication are design documents. The transfer feature is a local simulation, not bank execution. It does not enforce real-user identity or support arbitrary database connections. Keyword routing is only an early signal; query safety comes from the closed intent schema, fixed compiler, read-only query connection and scoped parameters. The execution route has a separate parser, account allowlist, explicit confirmation, balance check and atomic transaction.
