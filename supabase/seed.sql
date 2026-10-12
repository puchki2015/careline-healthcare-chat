-- Careline intentionally ships without environment-specific seed data.
--
-- Supabase CLI runs this file after all files in supabase/migrations during
-- `supabase db reset`. Keeping the file present makes the repository's
-- config.toml portable while avoiding fixed auth-user UUIDs, email addresses,
-- administrator accounts, patient records, or appointment data.
--
-- Create the first administrator separately by following the provisioning
-- instructions in the root README. Add disposable development fixtures only
-- in a local, uncommitted seed override or through the documented APIs.

select 'Careline schema installed; no seed records were created.' as seed_status;
