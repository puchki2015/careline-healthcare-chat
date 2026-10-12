-- Atomic persistence commands for one Careline LangGraph turn.
--
-- These functions are callable only by service_role. They serialize writes on
-- the parent chat_sessions row, so two simultaneous requests cannot allocate
-- the same sequence number. Browser roles retain read-only RLS access.

create or replace function public.careline_start_chat_turn(
  p_patient_id uuid,
  p_session_id uuid,
  p_request_id uuid,
  p_client_message_id uuid,
  p_content text,
  p_message_type text,
  p_model_name text,
  p_prompt_version text
)
returns table (
  outcome text,
  run_id uuid,
  input_message_id uuid,
  input_sequence bigint
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_status text;
  v_message_id uuid;
  v_sequence bigint;
  v_run_id uuid;
begin
  -- The row lock is the sequence allocator. It is held until this function's
  -- transaction commits, so concurrent turns for one session are serialized.
  select cs.status
    into v_status
  from public.chat_sessions as cs
  where cs.id = p_session_id
    and cs.patient_id = p_patient_id
  for update;

  if not found then
    return query select 'not_found'::text, null::uuid, null::uuid, null::bigint;
    return;
  end if;

  if v_status <> 'active' then
    return query select 'not_active'::text, null::uuid, null::uuid, null::bigint;
    return;
  end if;

  -- A client UUID identifies one user action. Return a neutral duplicate
  -- outcome instead of exposing an existing run or replaying a stored secret.
  if p_client_message_id is not null then
    select cm.id, cm.sequence_number
      into v_message_id, v_sequence
    from public.chat_messages as cm
    where cm.session_id = p_session_id
      and cm.patient_id = p_patient_id
      and cm.client_message_id = p_client_message_id;

    if found then
      return query select 'duplicate'::text, null::uuid, v_message_id, v_sequence;
      return;
    end if;
  end if;

  select coalesce(max(cm.sequence_number), 0) + 1
    into v_sequence
  from public.chat_messages as cm
  where cm.session_id = p_session_id;

  insert into public.chat_messages (
    session_id,
    patient_id,
    sequence_number,
    role,
    message_type,
    content,
    client_message_id
  )
  values (
    p_session_id,
    p_patient_id,
    v_sequence,
    'user',
    p_message_type,
    p_content,
    p_client_message_id
  )
  returning id into v_message_id;

  insert into public.chat_runs (
    session_id,
    patient_id,
    request_id,
    input_message_id,
    status,
    model_name,
    prompt_version
  )
  values (
    p_session_id,
    p_patient_id,
    p_request_id,
    v_message_id,
    'started',
    p_model_name,
    p_prompt_version
  )
  returning id into v_run_id;

  update public.chat_sessions
  set last_message_at = now()
  where id = p_session_id
    and patient_id = p_patient_id;

  return query select 'started'::text, v_run_id, v_message_id, v_sequence;
end;
$$;


create or replace function public.careline_complete_chat_turn(
  p_patient_id uuid,
  p_session_id uuid,
  p_run_id uuid,
  p_content text,
  p_message_type text,
  p_structured_data jsonb,
  p_intent text,
  p_decision_data jsonb,
  p_latency_ms numeric,
  p_tool_events jsonb
)
returns table (
  outcome text,
  output_message_id uuid
)
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
  select cr.status
    into v_run_status
  from public.chat_runs as cr
  where cr.id = p_run_id
    and cr.session_id = p_session_id
    and cr.patient_id = p_patient_id
  for update;

  if not found then
    return query select 'not_found'::text, null::uuid;
    return;
  end if;

  if v_run_status <> 'started' then
    return query select 'not_started'::text, null::uuid;
    return;
  end if;

  -- Use the same session lock as the start function before allocating the
  -- assistant sequence number.
  perform 1
  from public.chat_sessions as cs
  where cs.id = p_session_id
    and cs.patient_id = p_patient_id
  for update;

  select coalesce(max(cm.sequence_number), 0) + 1
    into v_sequence
  from public.chat_messages as cm
  where cm.session_id = p_session_id;

  insert into public.chat_messages (
    session_id,
    patient_id,
    sequence_number,
    role,
    message_type,
    content,
    structured_data
  )
  values (
    p_session_id,
    p_patient_id,
    v_sequence,
    'assistant',
    p_message_type,
    p_content,
    p_structured_data
  )
  returning id into v_message_id;

  -- Tool events contain only the sanitized dictionaries created by trusted
  -- graph nodes. Array order becomes the immutable event sequence.
  for v_event in
    select value from jsonb_array_elements(coalesce(p_tool_events, '[]'::jsonb))
  loop
    v_event_number := v_event_number + 1;
    insert into public.chat_tool_events (
      run_id,
      session_id,
      patient_id,
      sequence_number,
      node_name,
      tool_name,
      status,
      arguments,
      result,
      duration_ms,
      created_at,
      completed_at
    )
    values (
      p_run_id,
      p_session_id,
      p_patient_id,
      v_event_number,
      v_event ->> 'node_name',
      v_event ->> 'tool_name',
      'completed',
      nullif(v_event -> 'arguments', 'null'::jsonb),
      nullif(v_event -> 'result', 'null'::jsonb),
      (v_event ->> 'duration_ms')::numeric,
      v_now,
      v_now
    );
  end loop;

  update public.chat_runs
  set output_message_id = v_message_id,
      intent = p_intent,
      decision_data = p_decision_data,
      status = 'completed',
      latency_ms = p_latency_ms,
      completed_at = v_now
  where id = p_run_id;

  update public.chat_sessions
  set last_message_at = v_now
  where id = p_session_id
    and patient_id = p_patient_id;

  return query select 'completed'::text, v_message_id;
end;
$$;


create or replace function public.careline_fail_chat_turn(
  p_patient_id uuid,
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
declare
  v_updated_count integer;
begin
  update public.chat_runs
  set status = 'failed',
      error_code = p_error_code,
      latency_ms = p_latency_ms,
      completed_at = now()
  where id = p_run_id
    and session_id = p_session_id
    and patient_id = p_patient_id
    and status = 'started';

  get diagnostics v_updated_count = row_count;
  return v_updated_count = 1;
end;
$$;

revoke all on function public.careline_start_chat_turn(uuid, uuid, uuid, uuid, text, text, text, text)
  from public, anon, authenticated;
revoke all on function public.careline_complete_chat_turn(uuid, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb)
  from public, anon, authenticated;
revoke all on function public.careline_fail_chat_turn(uuid, uuid, uuid, text, numeric)
  from public, anon, authenticated;

grant execute on function public.careline_start_chat_turn(uuid, uuid, uuid, uuid, text, text, text, text)
  to service_role;
grant execute on function public.careline_complete_chat_turn(uuid, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb)
  to service_role;
grant execute on function public.careline_fail_chat_turn(uuid, uuid, uuid, text, numeric)
  to service_role;
