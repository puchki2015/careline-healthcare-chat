-- Allow the patient orchestrator to persist appointment-specialist turns.
--
-- The appointment feature introduced the top-level `manage_appointment`
-- intent, but the existing chat_runs check constraint still contained only
-- the earlier profile/admin intents. PostgreSQL therefore rejected an
-- otherwise successful appointment graph result during turn completion.

alter table public.chat_runs
  drop constraint chat_runs_intent_valid;

alter table public.chat_runs
  add constraint chat_runs_intent_valid check (
    intent is null
    or intent in (
      'read_profile',
      'update_profile',
      'create_patient',
      'manage_appointment',
      'unsupported'
    )
  );

comment on constraint chat_runs_intent_valid on public.chat_runs is
  'Allowed top-level patient and administrator orchestrator intents.';
