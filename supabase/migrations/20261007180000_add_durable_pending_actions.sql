-- Persist update confirmations so they survive API restarts and work across
-- multiple backend workers. Only SHA-256 token hashes are stored.

drop function public.careline_complete_chat_turn(
  uuid, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb
);

create function public.careline_complete_chat_turn(
  p_patient_id uuid,
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

  -- The pending row and proposal message commit together. A caller can never
  -- receive a usable proposal response whose durable action row is missing.
  if p_pending_action is not null then
    insert into public.chat_pending_actions (
      token_hash,
      session_id,
      patient_id,
      run_id,
      proposal_message_id,
      action_type,
      action_payload,
      expires_at
    )
    values (
      p_pending_action ->> 'token_hash',
      p_session_id,
      p_patient_id,
      p_run_id,
      v_message_id,
      p_pending_action ->> 'action_type',
      p_pending_action -> 'action_payload',
      (p_pending_action ->> 'expires_at')::timestamptz
    );
  end if;

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


create function public.careline_consume_pending_action(
  p_patient_id uuid,
  p_session_id uuid,
  p_token_hash text,
  p_decision text
)
returns table (
  outcome text,
  action_payload jsonb
)
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
  -- The row lock makes approval/rejection one-time even when two workers
  -- receive the same confirmation simultaneously.
  select pa.id, pa.action_payload, pa.expires_at, pa.consumed_at
    into v_action_id, v_payload, v_expires_at, v_consumed_at
  from public.chat_pending_actions as pa
  where pa.token_hash = p_token_hash
    and pa.patient_id = p_patient_id
    and pa.session_id = p_session_id
  for update;

  if not found or v_consumed_at is not null or v_expires_at <= now() then
    return query select 'unavailable'::text, null::jsonb;
    return;
  end if;

  if p_decision not in ('approved', 'rejected') then
    return query select 'unavailable'::text, null::jsonb;
    return;
  end if;

  update public.chat_pending_actions
  set decision = p_decision,
      consumed_at = now()
  where id = v_action_id;

  return query select 'consumed'::text, v_payload;
end;
$$;

revoke all on function public.careline_complete_chat_turn(
  uuid, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb, jsonb
) from public, anon, authenticated;
revoke all on function public.careline_consume_pending_action(uuid, uuid, text, text)
  from public, anon, authenticated;

grant execute on function public.careline_complete_chat_turn(
  uuid, uuid, uuid, text, text, jsonb, text, jsonb, numeric, jsonb, jsonb
) to service_role;
grant execute on function public.careline_consume_pending_action(uuid, uuid, text, text)
  to service_role;
