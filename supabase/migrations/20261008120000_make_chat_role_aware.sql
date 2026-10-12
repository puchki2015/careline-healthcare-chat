-- Convert the six existing patient-owned chat tables into one shared,
-- role-aware audit model. Existing rows are preserved and backfilled as
-- patient-initiated conversations.

create table public.user_roles (
  user_id     uuid not null references auth.users (id) on delete cascade,
  role        text not null,
  is_active   boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (user_id, role),
  constraint user_roles_role_valid check (role in ('patient', 'admin', 'doctor'))
);

create trigger user_roles_set_updated_at
  before update on public.user_roles
  for each row execute function public.set_updated_at();

create table public.admin_profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  email         text not null,
  display_name  text not null,
  is_active     boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint admin_profiles_email_length check (char_length(email) between 3 and 254),
  constraint admin_profiles_name_length check (char_length(display_name) between 1 and 100)
);

create trigger admin_profiles_set_updated_at
  before update on public.admin_profiles
  for each row execute function public.set_updated_at();

insert into public.user_roles (user_id, role)
select id, 'patient' from public.patients
on conflict (user_id, role) do nothing;

-- Future signups and email invitations receive their patient role in the
-- same Auth transaction that creates their patient profile.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.patients (id, email, full_name)
  values (new.id, new.email, new.raw_user_meta_data ->> 'full_name');

  insert into public.user_roles (user_id, role)
  values (new.id, 'patient');
  return new;
end;
$$;

-- Remove ownership foreign keys before replacing patient_id with a generic
-- authenticated initiator. subject_patient_id remains optional session data.
alter table public.chat_tool_events
  drop constraint chat_tool_events_run_session_patient_fk;
alter table public.chat_pending_actions
  drop constraint chat_pending_actions_run_session_patient_fk,
  drop constraint chat_pending_actions_proposal_message_fk;
alter table public.chat_runs
  drop constraint chat_runs_session_patient_fk,
  drop constraint chat_runs_input_message_fk,
  drop constraint chat_runs_output_message_fk,
  drop constraint chat_runs_id_session_patient_unique;
alter table public.chat_messages
  drop constraint chat_messages_session_patient_fk,
  drop constraint chat_messages_id_session_patient_unique;
alter table public.chat_session_context_sources
  drop constraint chat_context_target_session_patient_fk,
  drop constraint chat_context_source_session_patient_fk;
alter table public.chat_sessions
  drop constraint chat_sessions_id_patient_unique;

alter table public.chat_sessions rename column patient_id to subject_patient_id;
alter table public.chat_sessions alter column subject_patient_id drop not null;
alter table public.chat_sessions
  add column chat_initiated_by uuid,
  add column chat_initiator_role text;
update public.chat_sessions
set chat_initiated_by = subject_patient_id,
    chat_initiator_role = 'patient';
alter table public.chat_sessions
  alter column chat_initiated_by set not null,
  alter column chat_initiator_role set not null;

alter table public.chat_messages rename column patient_id to chat_initiated_by;
alter table public.chat_messages add column chat_initiator_role text;
update public.chat_messages set chat_initiator_role = 'patient';
alter table public.chat_messages alter column chat_initiator_role set not null;
alter table public.chat_messages
  add column sent_by_user_id uuid,
  add column sent_by_role text;
update public.chat_messages
set sent_by_user_id = case when role = 'user' then chat_initiated_by else null end,
    sent_by_role = case when role = 'user' then 'patient' else role end;
alter table public.chat_messages alter column sent_by_role set not null;

alter table public.chat_runs rename column patient_id to chat_initiated_by;
alter table public.chat_runs add column chat_initiator_role text;
update public.chat_runs set chat_initiator_role = 'patient';
alter table public.chat_runs alter column chat_initiator_role set not null;

alter table public.chat_tool_events rename column patient_id to chat_initiated_by;
alter table public.chat_tool_events add column chat_initiator_role text;
update public.chat_tool_events set chat_initiator_role = 'patient';
alter table public.chat_tool_events alter column chat_initiator_role set not null;

alter table public.chat_pending_actions rename column patient_id to chat_initiated_by;
alter table public.chat_pending_actions add column chat_initiator_role text;
update public.chat_pending_actions set chat_initiator_role = 'patient';
alter table public.chat_pending_actions alter column chat_initiator_role set not null;

alter table public.chat_session_context_sources rename column patient_id to chat_initiated_by;
alter table public.chat_session_context_sources add column chat_initiator_role text;
update public.chat_session_context_sources set chat_initiator_role = 'patient';
alter table public.chat_session_context_sources alter column chat_initiator_role set not null;

alter table public.chat_sessions
  add constraint chat_sessions_initiator_role_fk
    foreign key (chat_initiated_by, chat_initiator_role)
    references public.user_roles (user_id, role),
  add constraint chat_sessions_id_initiator_unique
    unique (id, chat_initiated_by, chat_initiator_role),
  add constraint chat_sessions_initiator_role_valid
    check (chat_initiator_role in ('patient', 'admin', 'doctor'));

alter table public.chat_messages
  add constraint chat_messages_session_initiator_fk
    foreign key (session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_sessions (id, chat_initiated_by, chat_initiator_role)
    on delete cascade,
  add constraint chat_messages_id_session_initiator_unique
    unique (id, session_id, chat_initiated_by, chat_initiator_role),
  add constraint chat_messages_sender_role_fk
    foreign key (sent_by_user_id, sent_by_role)
    references public.user_roles (user_id, role),
  add constraint chat_messages_sender_role_valid
    check (sent_by_role in ('patient', 'admin', 'doctor', 'assistant', 'system_notice')),
  add constraint chat_messages_sender_consistent
    check (
      (sent_by_role in ('patient', 'admin', 'doctor') and sent_by_user_id is not null)
      or (sent_by_role in ('assistant', 'system_notice') and sent_by_user_id is null)
    );

alter table public.chat_runs
  add constraint chat_runs_session_initiator_fk
    foreign key (session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_sessions (id, chat_initiated_by, chat_initiator_role)
    on delete cascade,
  add constraint chat_runs_input_message_initiator_fk
    foreign key (input_message_id, session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_messages (
      id, session_id, chat_initiated_by, chat_initiator_role
    ),
  add constraint chat_runs_output_message_initiator_fk
    foreign key (output_message_id, session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_messages (
      id, session_id, chat_initiated_by, chat_initiator_role
    ),
  add constraint chat_runs_id_session_initiator_unique
    unique (id, session_id, chat_initiated_by, chat_initiator_role);

alter table public.chat_tool_events
  add constraint chat_tool_events_run_session_initiator_fk
    foreign key (run_id, session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_runs (
      id, session_id, chat_initiated_by, chat_initiator_role
    ) on delete cascade;

alter table public.chat_pending_actions
  add constraint chat_pending_actions_run_session_initiator_fk
    foreign key (run_id, session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_runs (
      id, session_id, chat_initiated_by, chat_initiator_role
    ) on delete cascade,
  add constraint chat_pending_actions_proposal_message_initiator_fk
    foreign key (
      proposal_message_id, session_id, chat_initiated_by, chat_initiator_role
    ) references public.chat_messages (
      id, session_id, chat_initiated_by, chat_initiator_role
    );

alter table public.chat_session_context_sources
  add constraint chat_context_target_session_initiator_fk
    foreign key (session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_sessions (id, chat_initiated_by, chat_initiator_role)
    on delete cascade,
  add constraint chat_context_source_session_initiator_fk
    foreign key (source_session_id, chat_initiated_by, chat_initiator_role)
    references public.chat_sessions (id, chat_initiated_by, chat_initiator_role)
    on delete cascade;

alter table public.chat_messages drop constraint chat_messages_type_valid;
alter table public.chat_messages add constraint chat_messages_type_valid check (
  message_type in (
    'text', 'profile_result', 'information_request', 'confirmation_request',
    'confirmation_result', 'invitation_result', 'error', 'system_notice'
  )
);
alter table public.chat_runs drop constraint chat_runs_intent_valid;
alter table public.chat_runs add constraint chat_runs_intent_valid check (
  intent is null or intent in ('read_profile', 'update_profile', 'create_patient', 'unsupported')
);
alter table public.chat_pending_actions drop constraint chat_pending_actions_type_valid;
alter table public.chat_pending_actions add constraint chat_pending_actions_type_valid check (
  action_type in ('update_profile', 'create_patient', 'delete_account')
);

drop index public.chat_sessions_patient_recent_idx;
create index chat_sessions_initiator_recent_idx
  on public.chat_sessions (
    chat_initiated_by, chat_initiator_role, last_message_at desc, id desc
  );
create index chat_sessions_subject_patient_idx
  on public.chat_sessions (subject_patient_id, last_message_at desc)
  where subject_patient_id is not null;

drop policy "chat_sessions_select_own" on public.chat_sessions;
drop policy "chat_messages_select_own" on public.chat_messages;
create policy "chat_sessions_select_own"
  on public.chat_sessions for select to authenticated
  using ((select auth.uid()) = chat_initiated_by);
create policy "chat_messages_select_own"
  on public.chat_messages for select to authenticated
  using ((select auth.uid()) = chat_initiated_by);

alter table public.user_roles enable row level security;
alter table public.admin_profiles enable row level security;
revoke all on public.user_roles from anon, authenticated;
revoke all on public.admin_profiles from anon, authenticated;
grant select, insert, update, delete on public.user_roles to service_role;
grant select, insert, update, delete on public.admin_profiles to service_role;

-- Replace patient-specific RPC contracts with role-aware equivalents.
drop function public.careline_create_chat_session_with_context(uuid, text, text, text, integer);
drop function public.careline_start_chat_turn(uuid, uuid, uuid, uuid, text, text, text, text);
drop function public.careline_complete_chat_turn(
  uuid, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb, jsonb
);
drop function public.careline_fail_chat_turn(uuid, uuid, uuid, text, numeric);
drop function public.careline_consume_pending_action(uuid, uuid, text, text);
drop function public.careline_get_session_memory(uuid, uuid);

create function public.careline_create_chat_session_with_context(
  p_chat_initiated_by uuid,
  p_chat_initiator_role text,
  p_subject_patient_id uuid,
  p_title text,
  p_model_name text,
  p_prompt_version text,
  p_prior_session_limit integer
)
returns setof public.chat_sessions
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_session public.chat_sessions%rowtype;
begin
  if p_prior_session_limit is null or p_prior_session_limit < 0 or p_prior_session_limit > 10 then
    raise exception 'prior session limit is outside the allowed range' using errcode = '22023';
  end if;
  if not exists (
    select 1 from public.user_roles
    where user_id = p_chat_initiated_by
      and role = p_chat_initiator_role
      and is_active = true
  ) then
    return;
  end if;

  insert into public.chat_sessions (
    chat_initiated_by, chat_initiator_role, subject_patient_id,
    title, model_name, prompt_version
  ) values (
    p_chat_initiated_by, p_chat_initiator_role, p_subject_patient_id,
    p_title, p_model_name, p_prompt_version
  ) returning * into v_session;

  with recent_sources as (
    select
      source.id,
      row_number() over (
        order by source.last_message_at desc, source.id desc
      )::integer as source_position
    from public.chat_sessions as source
    where source.chat_initiated_by = p_chat_initiated_by
      and source.chat_initiator_role = p_chat_initiator_role
      and source.id <> v_session.id
      and source.status = 'closed'
      and source.summary is not null
      and btrim(source.summary) <> ''
    order by source.last_message_at desc, source.id desc
    limit p_prior_session_limit
  )
  insert into public.chat_session_context_sources (
    session_id, source_session_id, chat_initiated_by,
    chat_initiator_role, position, included_summary
  )
  select
    v_session.id, recent_sources.id, p_chat_initiated_by,
    p_chat_initiator_role, recent_sources.source_position, true
  from recent_sources;

  return next v_session;
end;
$$;

create function public.careline_start_chat_turn(
  p_chat_initiated_by uuid,
  p_chat_initiator_role text,
  p_session_id uuid,
  p_request_id uuid,
  p_client_message_id uuid,
  p_content text,
  p_message_type text,
  p_model_name text,
  p_prompt_version text
)
returns table (
  outcome text, run_id uuid, input_message_id uuid, input_sequence bigint
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_status text;
  v_existing_message_id uuid;
  v_existing_run_id uuid;
  v_existing_sequence bigint;
  v_message_id uuid;
  v_run_id uuid;
  v_sequence bigint;
begin
  select session.status into v_status
  from public.chat_sessions as session
  where session.id = p_session_id
    and session.chat_initiated_by = p_chat_initiated_by
    and session.chat_initiator_role = p_chat_initiator_role
  for update;
  if not found then
    return query select 'not_found'::text, null::uuid, null::uuid, null::bigint;
    return;
  end if;
  if v_status <> 'active' then
    return query select 'not_active'::text, null::uuid, null::uuid, null::bigint;
    return;
  end if;

  if p_client_message_id is not null then
    select message.id, run.id, message.sequence_number
      into v_existing_message_id, v_existing_run_id, v_existing_sequence
    from public.chat_messages as message
    left join public.chat_runs as run on run.input_message_id = message.id
    where message.session_id = p_session_id
      and message.client_message_id = p_client_message_id;
    if found then
      return query select
        'duplicate'::text, v_existing_run_id, v_existing_message_id, v_existing_sequence;
      return;
    end if;
  end if;

  if exists (
    select 1 from public.chat_runs
    where session_id = p_session_id and status = 'started'
  ) then
    return query select 'in_progress'::text, null::uuid, null::uuid, null::bigint;
    return;
  end if;

  select coalesce(max(sequence_number), 0) + 1 into v_sequence
  from public.chat_messages where session_id = p_session_id;

  insert into public.chat_messages (
    session_id, chat_initiated_by, chat_initiator_role, sequence_number,
    role, message_type, content, client_message_id, sent_by_user_id, sent_by_role
  ) values (
    p_session_id, p_chat_initiated_by, p_chat_initiator_role, v_sequence,
    'user', p_message_type, p_content, p_client_message_id,
    p_chat_initiated_by, p_chat_initiator_role
  ) returning id into v_message_id;

  insert into public.chat_runs (
    session_id, chat_initiated_by, chat_initiator_role, request_id,
    input_message_id, model_name, prompt_version
  ) values (
    p_session_id, p_chat_initiated_by, p_chat_initiator_role, p_request_id,
    v_message_id, p_model_name, p_prompt_version
  ) returning id into v_run_id;

  return query select 'started'::text, v_run_id, v_message_id, v_sequence;
end;
$$;

create function public.careline_complete_chat_turn(
  p_chat_initiated_by uuid,
  p_chat_initiator_role text,
  p_session_id uuid,
  p_run_id uuid,
  p_content text,
  p_message_type text,
  p_structured_data jsonb,
  p_intent text,
  p_decision_data jsonb,
  p_latency_ms numeric,
  p_tool_events jsonb,
  p_pending_action jsonb
)
returns table (outcome text, output_message_id uuid)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_run_status text;
  v_message_id uuid;
  v_sequence bigint;
  v_event jsonb;
  v_event_number integer := 0;
  v_now timestamptz := now();
begin
  select run.status into v_run_status
  from public.chat_runs as run
  where run.id = p_run_id
    and run.session_id = p_session_id
    and run.chat_initiated_by = p_chat_initiated_by
    and run.chat_initiator_role = p_chat_initiator_role
  for update;
  if not found then
    return query select 'not_found'::text, null::uuid;
    return;
  end if;
  if v_run_status <> 'started' then
    return query select 'not_started'::text, null::uuid;
    return;
  end if;

  perform 1 from public.chat_sessions
  where id = p_session_id
    and chat_initiated_by = p_chat_initiated_by
    and chat_initiator_role = p_chat_initiator_role
  for update;
  select coalesce(max(sequence_number), 0) + 1 into v_sequence
  from public.chat_messages where session_id = p_session_id;

  insert into public.chat_messages (
    session_id, chat_initiated_by, chat_initiator_role, sequence_number,
    role, message_type, content, structured_data, sent_by_user_id, sent_by_role
  ) values (
    p_session_id, p_chat_initiated_by, p_chat_initiator_role, v_sequence,
    'assistant', p_message_type, p_content, p_structured_data, null, 'assistant'
  ) returning id into v_message_id;

  if p_pending_action is not null then
    insert into public.chat_pending_actions (
      token_hash, session_id, chat_initiated_by, chat_initiator_role,
      run_id, proposal_message_id, action_type, action_payload, expires_at
    ) values (
      p_pending_action ->> 'token_hash', p_session_id,
      p_chat_initiated_by, p_chat_initiator_role, p_run_id, v_message_id,
      p_pending_action ->> 'action_type', p_pending_action -> 'action_payload',
      (p_pending_action ->> 'expires_at')::timestamptz
    );
  end if;

  for v_event in
    select value from jsonb_array_elements(coalesce(p_tool_events, '[]'::jsonb))
  loop
    v_event_number := v_event_number + 1;
    insert into public.chat_tool_events (
      run_id, session_id, chat_initiated_by, chat_initiator_role,
      sequence_number, node_name, tool_name, status, arguments,
      result, duration_ms, created_at, completed_at
    ) values (
      p_run_id, p_session_id, p_chat_initiated_by, p_chat_initiator_role,
      v_event_number, v_event ->> 'node_name', v_event ->> 'tool_name',
      'completed', nullif(v_event -> 'arguments', 'null'::jsonb),
      nullif(v_event -> 'result', 'null'::jsonb),
      (v_event ->> 'duration_ms')::numeric, v_now, v_now
    );
  end loop;

  update public.chat_runs set
    output_message_id = v_message_id,
    intent = p_intent,
    decision_data = p_decision_data,
    status = 'completed',
    latency_ms = p_latency_ms,
    completed_at = v_now
  where id = p_run_id;
  update public.chat_sessions set last_message_at = v_now
  where id = p_session_id;
  return query select 'completed'::text, v_message_id;
end;
$$;

create function public.careline_fail_chat_turn(
  p_chat_initiated_by uuid,
  p_chat_initiator_role text,
  p_session_id uuid,
  p_run_id uuid,
  p_error_code text,
  p_latency_ms numeric
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
begin
  update public.chat_runs set
    status = 'failed', error_code = p_error_code,
    latency_ms = p_latency_ms, completed_at = now()
  where id = p_run_id and session_id = p_session_id
    and chat_initiated_by = p_chat_initiated_by
    and chat_initiator_role = p_chat_initiator_role
    and status = 'started';
  return found;
end;
$$;

create function public.careline_consume_pending_action(
  p_chat_initiated_by uuid,
  p_chat_initiator_role text,
  p_session_id uuid,
  p_token_hash text,
  p_decision text
)
returns table (outcome text, action_payload jsonb)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_action_id uuid;
  v_payload jsonb;
  v_expires_at timestamptz;
  v_consumed_at timestamptz;
begin
  select action.id, action.action_payload, action.expires_at, action.consumed_at
    into v_action_id, v_payload, v_expires_at, v_consumed_at
  from public.chat_pending_actions as action
  where action.token_hash = p_token_hash
    and action.chat_initiated_by = p_chat_initiated_by
    and action.chat_initiator_role = p_chat_initiator_role
    and action.session_id = p_session_id
  for update;
  if not found or v_consumed_at is not null or v_expires_at <= now()
     or p_decision not in ('approved', 'rejected') then
    return query select 'unavailable'::text, null::jsonb;
    return;
  end if;
  update public.chat_pending_actions
  set decision = p_decision, consumed_at = now()
  where id = v_action_id;
  return query select 'consumed'::text, v_payload;
end;
$$;

create function public.careline_get_session_memory(
  p_chat_initiated_by uuid,
  p_chat_initiator_role text,
  p_session_id uuid
)
returns table ("position" integer, source_session_id uuid, summary text)
language sql
stable
security invoker
set search_path = ''
as $$
  select context.position, context.source_session_id, source.summary
  from public.chat_session_context_sources as context
  join public.chat_sessions as source
    on source.id = context.source_session_id
   and source.chat_initiated_by = context.chat_initiated_by
   and source.chat_initiator_role = context.chat_initiator_role
  where context.session_id = p_session_id
    and context.chat_initiated_by = p_chat_initiated_by
    and context.chat_initiator_role = p_chat_initiator_role
    and context.included_summary = true
    and source.summary is not null
  order by context.position;
$$;

revoke all on function public.careline_create_chat_session_with_context(
  uuid, text, uuid, text, text, text, integer
) from public, anon, authenticated;
revoke all on function public.careline_start_chat_turn(
  uuid, text, uuid, uuid, uuid, text, text, text, text
) from public, anon, authenticated;
revoke all on function public.careline_complete_chat_turn(
  uuid, text, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb, jsonb
) from public, anon, authenticated;
revoke all on function public.careline_fail_chat_turn(uuid, text, uuid, uuid, text, numeric)
  from public, anon, authenticated;
revoke all on function public.careline_consume_pending_action(uuid, text, uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.careline_get_session_memory(uuid, text, uuid)
  from public, anon, authenticated;

grant execute on function public.careline_create_chat_session_with_context(
  uuid, text, uuid, text, text, text, integer
) to service_role;
grant execute on function public.careline_start_chat_turn(
  uuid, text, uuid, uuid, uuid, text, text, text, text
) to service_role;
grant execute on function public.careline_complete_chat_turn(
  uuid, text, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb, jsonb
) to service_role;
grant execute on function public.careline_fail_chat_turn(uuid, text, uuid, uuid, text, numeric)
  to service_role;
grant execute on function public.careline_consume_pending_action(uuid, text, uuid, text, text)
  to service_role;
grant execute on function public.careline_get_session_memory(uuid, text, uuid)
  to service_role;
