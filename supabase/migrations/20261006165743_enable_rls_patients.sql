alter table public.patients enable row level security;

-- Anonymous visitors get nothing.
revoke all on public.patients from anon;

-- Logged-in users can read their own row.
create policy "patients_select_own"
  on public.patients for select
  to authenticated
  using ((select auth.uid()) = id);

-- Logged-in users can update their own row.
create policy "patients_update_own"
  on public.patients for update
  to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

-- Column-level protection: only whitelisted columns are updatable.
-- id, email, created_at and updated_at cannot be changed by the client.
revoke update on public.patients from authenticated;
grant update (full_name, phone, date_of_birth, gender, address, emergency_contact)
  on public.patients to authenticated;