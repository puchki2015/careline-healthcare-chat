-- Durable Careline conversation history and LangGraph execution audit tables.
--
-- Security model:
--   * authenticated patients may SELECT only their own sessions/messages;
--   * all writes go through the trusted FastAPI backend using service_role;
--   * runs, tool events, pending actions and context-source records are
--     backend-only and are never exposed directly to authenticated clients;
--   * access tokens, provider keys and plaintext confirmation tokens must
--     never be stored in these tables.

create table public.chat_sessions (
  id                 uuid primary key default gen_random_uuid(),
  patient_id         uuid not null references public.patients (id) on delete cascade,
  title              text,
  status             text not null default 'active',
  summary            text,
  model_name         text not null,
  prompt_version     text not null,
  started_at         timestamptz not null default now(),
  last_message_at    timestamptz not null default now(),
  ended_at           timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),

  constraint chat_sessions_id_patient_unique unique (id, patient_id),
  constraint chat_sessions_title_length
    check (title is null or char_length(title) between 1 and 200),
  constraint chat_sessions_status_valid
    check (status in ('active', 'closed', 'archived')),
  constraint chat_sessions_summary_length
    check (summary is null or char_length(summary) <= 20000),
  constraint chat_sessions_model_name_length
    check (char_length(model_name) between 1 and 100),
  constraint chat_sessions_prompt_version_length
    check (char_length(prompt_version) between 1 and 100),
  constraint chat_sessions_time_order
    check (
      last_message_at >= started_at
      and (ended_at is null or ended_at >= started_at)
    ),
  constraint chat_sessions_active_not_ended
    check (status <> 'active' or ended_at is null)
);

comment on table public.chat_sessions is
  'One patient-owned Careline conversation. Full messages live in chat_messages.';
comment on column public.chat_sessions.summary is
  'Bounded conversation summary for future model context; never a source of truth for profile data.';
comment on column public.chat_sessions.prompt_version is
  'Application-defined prompt version/hash, not the full system prompt.';

create trigger chat_sessions_set_updated_at
  before update on public.chat_sessions
  for each row execute function public.set_updated_at();


create table public.chat_messages (
  id                 uuid primary key default gen_random_uuid(),
  session_id         uuid not null,
  patient_id         uuid not null,
  sequence_number    bigint not null,
  role               text not null,
  message_type       text not null default 'text',
  content            text not null,
  structured_data    jsonb,
  client_message_id  uuid,
  created_at         timestamptz not null default now(),

  constraint chat_messages_session_patient_fk
    foreign key (session_id, patient_id)
    references public.chat_sessions (id, patient_id)
    on delete cascade,
  constraint chat_messages_id_session_patient_unique
    unique (id, session_id, patient_id),
  constraint chat_messages_session_sequence_unique
    unique (session_id, sequence_number),
  constraint chat_messages_client_id_unique
    unique (session_id, client_message_id),
  constraint chat_messages_sequence_positive
    check (sequence_number > 0),
  constraint chat_messages_role_valid
    check (role in ('user', 'assistant', 'system_notice')),
  constraint chat_messages_type_valid
    check (
      message_type in (
        'text',
        'profile_result',
        'confirmation_request',
        'confirmation_result',
        'error',
        'system_notice'
      )
    ),
  constraint chat_messages_content_length
    check (char_length(content) between 1 and 100000),
  constraint chat_messages_structured_data_object
    check (structured_data is null or jsonb_typeof(structured_data) = 'object')
);

comment on table public.chat_messages is
  'Ordered user-visible messages for a Careline chat session.';
comment on column public.chat_messages.client_message_id is
  'Frontend-generated UUID used to prevent duplicate processing after retries.';
comment on column public.chat_messages.structured_data is
  'Typed UI data such as profile results; may contain PHI and is protected by RLS.';


create table public.chat_runs (
  id                    uuid primary key default gen_random_uuid(),
  session_id            uuid not null,
  patient_id            uuid not null,
  request_id            uuid not null unique,
  input_message_id      uuid not null,
  output_message_id     uuid,
  intent                 text,
  status                 text not null default 'started',
  model_name             text,
  prompt_version         text,
  decision_data          jsonb,
  input_tokens           integer,
  output_tokens          integer,
  latency_ms             numeric(12, 2),
  provider_response_id   text,
  error_code             text,
  created_at             timestamptz not null default now(),
  completed_at           timestamptz,

  constraint chat_runs_session_patient_fk
    foreign key (session_id, patient_id)
    references public.chat_sessions (id, patient_id)
    on delete cascade,
  constraint chat_runs_input_message_fk
    foreign key (input_message_id, session_id, patient_id)
    references public.chat_messages (id, session_id, patient_id),
  constraint chat_runs_output_message_fk
    foreign key (output_message_id, session_id, patient_id)
    references public.chat_messages (id, session_id, patient_id),
  constraint chat_runs_id_session_patient_unique
    unique (id, session_id, patient_id),
  constraint chat_runs_intent_valid
    check (
      intent is null
      or intent in ('read_profile', 'update_profile', 'unsupported')
    ),
  constraint chat_runs_status_valid
    check (status in ('started', 'completed', 'failed')),
  constraint chat_runs_decision_data_object
    check (decision_data is null or jsonb_typeof(decision_data) = 'object'),
  constraint chat_runs_token_counts_nonnegative
    check (
      (input_tokens is null or input_tokens >= 0)
      and (output_tokens is null or output_tokens >= 0)
    ),
  constraint chat_runs_latency_nonnegative
    check (latency_ms is null or latency_ms >= 0),
  constraint chat_runs_model_name_length
    check (model_name is null or char_length(model_name) between 1 and 100),
  constraint chat_runs_prompt_version_length
    check (prompt_version is null or char_length(prompt_version) between 1 and 100),
  constraint chat_runs_provider_response_id_length
    check (provider_response_id is null or char_length(provider_response_id) <= 255),
  constraint chat_runs_error_code_length
    check (error_code is null or char_length(error_code) <= 100),
  constraint chat_runs_completion_consistent
    check (
      (status = 'started' and completed_at is null)
      or (status in ('completed', 'failed') and completed_at is not null)
    )
);

comment on table public.chat_runs is
  'Backend-only record of one LangGraph execution initiated by one patient message.';
comment on column public.chat_runs.decision_data is
  'Validated structured classifier output; may contain PHI and is not granted to patient clients.';
comment on column public.chat_runs.error_code is
  'Safe application/provider category only; raw exception text is prohibited.';


create table public.chat_tool_events (
  id                 uuid primary key default gen_random_uuid(),
  run_id             uuid not null,
  session_id         uuid not null,
  patient_id         uuid not null,
  sequence_number    integer not null,
  node_name          text not null,
  tool_name          text not null,
  status             text not null default 'started',
  arguments          jsonb,
  result             jsonb,
  duration_ms        numeric(12, 2),
  error_code         text,
  created_at         timestamptz not null default now(),
  completed_at       timestamptz,

  constraint chat_tool_events_run_session_patient_fk
    foreign key (run_id, session_id, patient_id)
    references public.chat_runs (id, session_id, patient_id)
    on delete cascade,
  constraint chat_tool_events_run_sequence_unique
    unique (run_id, sequence_number),
  constraint chat_tool_events_sequence_positive
    check (sequence_number > 0),
  constraint chat_tool_events_node_name_length
    check (char_length(node_name) between 1 and 100),
  constraint chat_tool_events_tool_name_length
    check (char_length(tool_name) between 1 and 100),
  constraint chat_tool_events_status_valid
    check (status in ('started', 'completed', 'failed')),
  constraint chat_tool_events_arguments_object
    check (arguments is null or jsonb_typeof(arguments) = 'object'),
  constraint chat_tool_events_result_object
    check (result is null or jsonb_typeof(result) = 'object'),
  constraint chat_tool_events_duration_nonnegative
    check (duration_ms is null or duration_ms >= 0),
  constraint chat_tool_events_error_code_length
    check (error_code is null or char_length(error_code) <= 100),
  constraint chat_tool_events_completion_consistent
    check (
      (status = 'started' and completed_at is null)
      or (status in ('completed', 'failed') and completed_at is not null)
    )
);

comment on table public.chat_tool_events is
  'Backend-only ordered LangGraph node/tool events with sanitized arguments and results.';


create table public.chat_pending_actions (
  id                   uuid primary key default gen_random_uuid(),
  token_hash           text not null unique,
  session_id           uuid not null,
  patient_id           uuid not null,
  run_id               uuid not null,
  proposal_message_id  uuid not null,
  action_type          text not null,
  action_payload       jsonb not null,
  expires_at           timestamptz not null,
  decision             text,
  consumed_at          timestamptz,
  created_at           timestamptz not null default now(),

  constraint chat_pending_actions_run_session_patient_fk
    foreign key (run_id, session_id, patient_id)
    references public.chat_runs (id, session_id, patient_id)
    on delete cascade,
  constraint chat_pending_actions_proposal_message_fk
    foreign key (proposal_message_id, session_id, patient_id)
    references public.chat_messages (id, session_id, patient_id),
  constraint chat_pending_actions_token_hash_valid
    check (token_hash ~ '^[0-9a-f]{64}$'),
  constraint chat_pending_actions_type_valid
    check (action_type in ('update_profile', 'delete_account')),
  constraint chat_pending_actions_payload_object
    check (jsonb_typeof(action_payload) = 'object'),
  constraint chat_pending_actions_expiration_valid
    check (expires_at > created_at),
  constraint chat_pending_actions_decision_valid
    check (decision is null or decision in ('approved', 'rejected')),
  constraint chat_pending_actions_consumption_consistent
    check (
      (decision is null and consumed_at is null)
      or (decision is not null and consumed_at is not null)
    )
);

comment on table public.chat_pending_actions is
  'Backend-only durable confirmation actions. token_hash is SHA-256; raw tokens are never stored.';
comment on column public.chat_pending_actions.action_payload is
  'Previously validated proposed operation; may contain PHI and is backend-only.';


create table public.chat_session_context_sources (
  id                        uuid primary key default gen_random_uuid(),
  session_id                uuid not null,
  source_session_id         uuid not null,
  patient_id                uuid not null,
  position                  integer not null,
  included_summary          boolean not null default true,
  included_from_sequence    bigint,
  included_to_sequence      bigint,
  created_at                timestamptz not null default now(),

  constraint chat_context_target_session_patient_fk
    foreign key (session_id, patient_id)
    references public.chat_sessions (id, patient_id)
    on delete cascade,
  constraint chat_context_source_session_patient_fk
    foreign key (source_session_id, patient_id)
    references public.chat_sessions (id, patient_id)
    on delete cascade,
  constraint chat_context_source_unique
    unique (session_id, source_session_id),
  constraint chat_context_position_unique
    unique (session_id, position),
  constraint chat_context_source_not_target
    check (session_id <> source_session_id),
  constraint chat_context_position_positive
    check (position > 0),
  constraint chat_context_message_range_consistent
    check (
      (
        included_from_sequence is null
        and included_to_sequence is null
      )
      or (
        included_from_sequence > 0
        and included_to_sequence >= included_from_sequence
      )
    )
);

comment on table public.chat_session_context_sources is
  'Backend-only record of previous sessions/summaries used to bootstrap a new session.';


-- Query paths used by history lists, cursor pagination and execution audits.
create index chat_sessions_patient_recent_idx
  on public.chat_sessions (patient_id, last_message_at desc, id desc);

create index chat_messages_patient_recent_idx
  on public.chat_messages (patient_id, created_at desc, id desc);

create index chat_runs_session_recent_idx
  on public.chat_runs (session_id, created_at desc, id desc);

create index chat_tool_events_session_recent_idx
  on public.chat_tool_events (session_id, created_at desc, id desc);

create index chat_pending_actions_patient_expiry_idx
  on public.chat_pending_actions (patient_id, expires_at)
  where consumed_at is null;

create index chat_context_source_session_idx
  on public.chat_session_context_sources (source_session_id);


-- Every table is in the exposed public schema, so RLS is enabled even when a
-- table currently has no patient-facing grants. Internal tables therefore
-- remain protected if grants are changed accidentally in a later migration.
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;
alter table public.chat_runs enable row level security;
alter table public.chat_tool_events enable row level security;
alter table public.chat_pending_actions enable row level security;
alter table public.chat_session_context_sources enable row level security;

revoke all on public.chat_sessions from anon, authenticated;
revoke all on public.chat_messages from anon, authenticated;
revoke all on public.chat_runs from anon, authenticated;
revoke all on public.chat_tool_events from anon, authenticated;
revoke all on public.chat_pending_actions from anon, authenticated;
revoke all on public.chat_session_context_sources from anon, authenticated;

-- Patients may retrieve only the history needed by their UI. Writes are
-- intentionally backend-only so a direct Supabase client cannot fabricate an
-- assistant message, tool event, model decision or confirmation record.
grant select on public.chat_sessions to authenticated;
grant select on public.chat_messages to authenticated;

create policy "chat_sessions_select_own"
  on public.chat_sessions for select
  to authenticated
  using ((select auth.uid()) = patient_id);

create policy "chat_messages_select_own"
  on public.chat_messages for select
  to authenticated
  using ((select auth.uid()) = patient_id);

-- The service-role key remains server-side and bypasses RLS. Explicit grants
-- document exactly which tables the FastAPI persistence layer may mutate.
grant select, insert, update, delete on public.chat_sessions to service_role;
grant select, insert, update, delete on public.chat_messages to service_role;
grant select, insert, update, delete on public.chat_runs to service_role;
grant select, insert, update, delete on public.chat_tool_events to service_role;
grant select, insert, update, delete on public.chat_pending_actions to service_role;
grant select, insert, update, delete on public.chat_session_context_sources to service_role;
