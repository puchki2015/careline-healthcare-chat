# Careline Supabase database

This directory is the complete, ordered database setup for Careline.

## Contents

- `config.toml` configures the Supabase CLI development stack.
- `migrations/` contains every required schema change in timestamp order.
- `seed.sql` is deliberately data-free and makes `supabase db reset` portable.

Do not run individual migration files manually or combine them into one large SQL script. The
Supabase migration ledger applies each file once and preserves the upgrade path for existing
environments.

## Install into a new hosted Supabase project

From the repository root:

```powershell
npx supabase login
npx supabase link --project-ref <new-project-ref>
npx supabase migration list
npx supabase db push --dry-run
npx supabase db push
npx supabase db lint --linked
```

The first dry run must list all migration files. After the push, a second dry run should report
`Remote database is up to date`.

## Rebuild a disposable local environment

Docker Desktop must be running because the Supabase CLI local stack uses containers:

```powershell
npx supabase start
npx supabase db reset
npx supabase db lint
```

`db reset` destroys only the local Supabase development database and then replays every migration
followed by `seed.sql`. Never use a destructive reset against a hosted environment.

## Environment-neutral rules

- No project URL, API key, service-role key, password, email address, or fixed auth-user UUID is
  stored in these SQL files.
- Migrations contain schema, functions, constraints, grants, triggers, indexes, and RLS policies.
- Real administrators are provisioned after deployment because their records reference an actual
  `auth.users` identity in that target project.
- Providers and appointment slots are business data and are created through protected admin APIs,
  not through migrations.
- Existing databases must use `npx supabase db push`; do not replay old files manually.
- If a database was created outside the migration chain, reconcile its schema and migration ledger
  before pushing. Do not mark a migration applied until its objects have been verified.

## Migration manifest

1. `20261006165524_create_patients_table.sql`
2. `20261006165647_create_patient_on_signup.sql`
3. `20261006165722_add_updated_at_trigger.sql`
4. `20261006165743_enable_rls_patients.sql`
5. `20261007120000_create_chat_history_tables.sql`
6. `20261007160000_add_atomic_chat_turn_functions.sql`
7. `20261007170000_serialize_chat_session_turns.sql`
8. `20261007180000_add_durable_pending_actions.sql`
9. `20261007190000_add_cross_session_memory.sql`
10. `20261008120000_make_chat_role_aware.sql`
11. `20261009120000_create_appointment_coordination.sql`
12. `20261010120000_allow_appointment_chat_intent.sql`

Together these migrations create the patient profile model, signup and timestamp triggers, RLS,
the six durable role-aware chat tables and transaction functions, administrator roles/profiles,
and appointment provider, availability, appointment, history, and transaction objects.
