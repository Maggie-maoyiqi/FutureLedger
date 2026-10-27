-- PostgreSQL design baseline, not a deployed migration.
-- Runtime roles, RLS policies, grants, balance/deferred ledger triggers are follow-up work.
BEGIN;
CREATE SCHEMA IF NOT EXISTS bank;
CREATE SCHEMA IF NOT EXISTS app;
CREATE TABLE app.users (
 id text PRIMARY KEY, username text UNIQUE NOT NULL, password_hash text NOT NULL,
 role text NOT NULL DEFAULT 'customer',
 created_at timestamptz NOT NULL DEFAULT now()
 -- Store only a salted, slow password hash (e.g. Argon2id/scrypt); never persist plaintext passwords.
);
CREATE TABLE bank.accounts (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES app.users(id),
 tail4 char(4) NOT NULL, currency char(3) NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 opening_balance_cents bigint NOT NULL, balance_cents bigint NOT NULL CHECK(balance_cents>=0),
 as_of timestamptz NOT NULL, UNIQUE(user_id,id)
);
CREATE TABLE bank.recipients (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES app.users(id), name text NOT NULL,
 alias text, bank_ref text NOT NULL, tail4 char(4) NOT NULL, verified boolean NOT NULL DEFAULT false,
 UNIQUE(user_id,id)
);
CREATE TABLE bank.transfers (
 id text PRIMARY KEY, user_id text NOT NULL, account_id text NOT NULL,
 recipient_id text NOT NULL, amount_cents bigint NOT NULL CHECK(amount_cents>0),
 fee_cents bigint NOT NULL DEFAULT 0 CHECK(fee_cents>=0),
 idempotency_key text NOT NULL, status text NOT NULL CHECK(status IN ('PENDING','SUCCEEDED','FAILED')),
 scheduled_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,idempotency_key),
 FOREIGN KEY(user_id,account_id) REFERENCES bank.accounts(user_id,id),
 FOREIGN KEY(user_id,recipient_id) REFERENCES bank.recipients(user_id,id)
);
CREATE TABLE bank.transactions (
 id text PRIMARY KEY, user_id text NOT NULL, account_id text NOT NULL,
 external_id text NOT NULL, amount_cents bigint NOT NULL CHECK(amount_cents>0),
 direction text NOT NULL CHECK(direction IN ('IN','OUT')),
 kind text NOT NULL CHECK(kind IN ('expense','refund','income','transfer')),
 status text NOT NULL CHECK(status IN ('POSTED','PENDING','FAILED')),
 category text NOT NULL, merchant text NOT NULL, memo text NOT NULL DEFAULT '',
 posted_at timestamptz NOT NULL, refund_of text REFERENCES bank.transactions(id),
 UNIQUE(account_id,external_id), FOREIGN KEY(user_id,account_id) REFERENCES bank.accounts(user_id,id)
);
CREATE INDEX transaction_scope_date ON bank.transactions(user_id,posted_at);
CREATE INDEX transaction_category_date ON bank.transactions(user_id,category,posted_at);
CREATE TABLE bank.journal_entries (
 id text PRIMARY KEY, business_ref text UNIQUE NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE bank.journal_lines (
 id text PRIMARY KEY, entry_id text NOT NULL REFERENCES bank.journal_entries(id),
 ledger_account_ref text NOT NULL, signed_cents bigint NOT NULL CHECK(signed_cents<>0)
 -- Bank transaction must check SUM(signed_cents)=0 per entry with a deferred trigger.
 -- Include external clearing account; never check a row in isolation to prove balance.
);
CREATE TABLE bank.mandates (
 id text PRIMARY KEY, user_id text NOT NULL, account_id text NOT NULL, merchant text NOT NULL,
 amount_cents bigint NOT NULL CHECK(amount_cents>=0), period text NOT NULL,
 next_due date, status text NOT NULL CHECK(status IN ('ACTIVE','CANCELLED','EXPIRED')),
 can_cancel boolean NOT NULL, as_of timestamptz NOT NULL,
 UNIQUE(user_id,id), FOREIGN KEY(user_id,account_id) REFERENCES bank.accounts(user_id,id)
);
CREATE TABLE app.preferences (
 user_id text PRIMARY KEY REFERENCES app.users(id), min_balance_cents bigint NOT NULL CHECK(min_balance_cents>=0),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.protected_mandates (
 user_id text NOT NULL, mandate_id text NOT NULL,
 PRIMARY KEY(user_id,mandate_id), FOREIGN KEY(user_id,mandate_id) REFERENCES bank.mandates(user_id,id)
);
CREATE TABLE app.subscription_candidates (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES app.users(id), merchant text NOT NULL,
 confidence numeric(4,3) CHECK(confidence BETWEEN 0 AND 1), evidence_transaction_ids jsonb NOT NULL,
 classification_version int NOT NULL, status text NOT NULL
);
CREATE TABLE app.datasets (
 id text PRIMARY KEY, semantic_version int NOT NULL, definition jsonb NOT NULL,
 published_at timestamptz NOT NULL
);
CREATE TABLE app.dataset_grants (
 user_id text NOT NULL, dataset_id text NOT NULL REFERENCES app.datasets(id), account_id text NOT NULL,
 PRIMARY KEY(user_id,dataset_id,account_id),
 FOREIGN KEY(user_id,account_id) REFERENCES bank.accounts(user_id,id)
);
CREATE TABLE app.sessions (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES app.users(id), dataset_id text REFERENCES app.datasets(id),
 semantic_version int NOT NULL, expires_at timestamptz NOT NULL, UNIQUE(user_id,id)
);
CREATE TABLE app.messages (
 id text PRIMARY KEY, user_id text NOT NULL, session_id text NOT NULL, role text NOT NULL,
 content jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,session_id) REFERENCES app.sessions(user_id,id)
);
CREATE TABLE app.goals (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES app.users(id), target_cents bigint NOT NULL CHECK(target_cents>0),
 target_date date NOT NULL, constraints jsonb NOT NULL, UNIQUE(user_id,id)
);
CREATE TABLE app.plans (
 id text NOT NULL, version int NOT NULL CHECK(version>0), user_id text NOT NULL REFERENCES app.users(id),
 goal_id text, status text NOT NULL, inputs jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(id,version), UNIQUE(user_id,id,version),
 FOREIGN KEY(user_id,goal_id) REFERENCES app.goals(user_id,id)
);
CREATE TABLE app.cashflow_events (
 id text PRIMARY KEY, user_id text NOT NULL, plan_id text NOT NULL, plan_version int NOT NULL,
 event_date date NOT NULL, signed_cents bigint NOT NULL, source text NOT NULL,
 assumption boolean NOT NULL, source_ref text,
 FOREIGN KEY(user_id,plan_id,plan_version) REFERENCES app.plans(user_id,id,version)
);
CREATE TABLE app.plan_nodes (
 id text PRIMARY KEY, plan_id text NOT NULL, plan_version int NOT NULL,
 tool_name text NOT NULL, inputs jsonb NOT NULL, depends_on jsonb NOT NULL DEFAULT '[]', status text NOT NULL,
 FOREIGN KEY(plan_id,plan_version) REFERENCES app.plans(id,version)
);
CREATE TABLE app.operations (
 id text PRIMARY KEY, user_id text NOT NULL, account_id text NOT NULL,
 plan_id text NOT NULL, plan_version int NOT NULL, kind text NOT NULL,
 params jsonb NOT NULL, params_hash text NOT NULL, idempotency_key text NOT NULL,
 risk_level text NOT NULL CHECK(risk_level IN ('YELLOW','RED')), status text NOT NULL,
 bank_ref text, scheduled_at timestamptz, lease_owner text, lease_until timestamptz,
 updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,idempotency_key), UNIQUE(user_id,id),
 FOREIGN KEY(user_id,account_id) REFERENCES bank.accounts(user_id,id),
 FOREIGN KEY(user_id,plan_id,plan_version) REFERENCES app.plans(user_id,id,version)
);
CREATE TABLE app.authorizations (
 id text PRIMARY KEY, user_id text NOT NULL, operation_id text NOT NULL, params_hash text NOT NULL,
 plan_version int NOT NULL, confirmed_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 auth_ref text, used_at timestamptz,
 FOREIGN KEY(user_id,operation_id) REFERENCES app.operations(user_id,id)
);
CREATE TABLE app.quota_days (
 user_id text NOT NULL REFERENCES app.users(id), business_date date NOT NULL,
 PRIMARY KEY(user_id,business_date)
 -- Executor locks this row, then sums reservations for all user's accounts.
);
CREATE TABLE app.quota_reservations (
 operation_id text PRIMARY KEY, user_id text NOT NULL, business_date date NOT NULL,
 amount_cents bigint NOT NULL CHECK(amount_cents>0), state text NOT NULL CHECK(state IN ('RESERVED','COMMITTED','RELEASED')),
 FOREIGN KEY(user_id,operation_id) REFERENCES app.operations(user_id,id),
 FOREIGN KEY(user_id,business_date) REFERENCES app.quota_days(user_id,business_date)
);
CREATE TABLE app.outbox (
 id bigserial PRIMARY KEY, operation_id text NOT NULL REFERENCES app.operations(id),
 event_type text NOT NULL, payload jsonb NOT NULL, available_at timestamptz NOT NULL DEFAULT now(),
 lease_owner text, lease_until timestamptz, dispatched_at timestamptz, attempts int NOT NULL DEFAULT 0,
 UNIQUE(operation_id,event_type)
);
CREATE TABLE app.audit_events (
 id bigserial PRIMARY KEY, user_id text NOT NULL REFERENCES app.users(id), operation_id text,
 event_type text NOT NULL, redacted_payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
