# Demo cooperatives for subscription plans

`scripts/seed_demo_planes.sql` is a copy-paste seed for the Supabase SQL Editor. It reads every existing `plan_suscripcion` row and inserts three demo cooperatives, active subscriptions, and administrator logins per plan. It does not create or update plans, or update/delete existing records.

## Run in Supabase

1. Select the intended Supabase project and confirm it is a disposable demo database with no real users.
2. Open its SQL Editor and paste the complete contents of `scripts/seed_demo_planes.sql`.
3. Run the statement once. It is a single PostgreSQL `DO` block, so a detected conflict raises an exception and rolls back the whole statement.

The script uses PostgreSQL's built-in SHA-256 function and does not install or depend on an extension. It contains no `psql` commands.

## Demo logins

| Item | Value |
|---|---|
| Cooperatives | Three for every existing plan row, regardless of plan status |
| Subscription | `ACTIVA`, linked to that exact plan row |
| Administrator | One active `ADMINISTRADOR` user per cooperative |
| Email | `demo-plan-<slug>-<first-8-hex-of-sha256-plan-code>-<01..03>@example.com` |
| Password | `DemoOnly-SI2-2026!` |

The password is public in this repository and identical for every demo account. **Do not run this seed against a database containing real users or data.**

If a deterministic demo name or email already exists with conflicting data, the SQL raises a descriptive exception and rolls back instead of overwriting it. Correct the conflict manually before rerunning.
