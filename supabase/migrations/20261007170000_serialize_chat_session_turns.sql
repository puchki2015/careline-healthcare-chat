-- Allow only one executing LangGraph turn per conversation at a time.
-- The parent-session lock makes the active-run check and insert atomic.

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

  -- Check the idempotency key first so a retry receives the more precise
  -- duplicate result even when another turn is currently executing.
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

  -- A conversation is ordered, so a second distinct request must wait for the
  -- current run to complete/fail instead of observing incomplete context.
  if exists (
    select 1
    from public.chat_runs as cr
    where cr.session_id = p_session_id
      and cr.patient_id = p_patient_id
      and cr.status = 'started'
  ) then
    return query select 'in_progress'::text, null::uuid, null::uuid, null::bigint;
    return;
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

revoke all on function public.careline_start_chat_turn(uuid, uuid, uuid, uuid, text, text, text, text)
  from public, anon, authenticated;
grant execute on function public.careline_start_chat_turn(uuid, uuid, uuid, uuid, text, text, text, text)
  to service_role;
