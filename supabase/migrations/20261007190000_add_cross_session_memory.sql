-- Give each newly created conversation bounded, auditable memory of recent
-- closed conversations. Summaries remain in chat_sessions.summary; the
-- existing chat_session_context_sources table records exactly what was used.

create function public.careline_create_chat_session_with_context(
  p_patient_id uuid,
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
  if p_prior_session_limit is null
     or p_prior_session_limit < 0
     or p_prior_session_limit > 10 then
    raise exception 'prior session limit is outside the allowed range'
      using errcode = '22023';
  end if;

  insert into public.chat_sessions (
    patient_id,
    title,
    model_name,
    prompt_version
  )
  values (
    p_patient_id,
    p_title,
    p_model_name,
    p_prompt_version
  )
  returning * into v_session;

  -- Select only closed sessions with useful summaries. position=1 is the
  -- newest source. The insert and new session commit in one transaction, so
  -- an API worker cannot create a partially audited context set.
  with recent_sources as (
    select
      source.id as source_session_id,
      row_number() over (
        order by source.last_message_at desc, source.id desc
      )::integer as source_position
    from public.chat_sessions as source
    where source.patient_id = p_patient_id
      and source.id <> v_session.id
      and source.status = 'closed'
      and source.summary is not null
      and btrim(source.summary) <> ''
    order by source.last_message_at desc, source.id desc
    limit p_prior_session_limit
  )
  insert into public.chat_session_context_sources (
    session_id,
    source_session_id,
    patient_id,
    position,
    included_summary
  )
  select
    v_session.id,
    recent_sources.source_session_id,
    p_patient_id,
    recent_sources.source_position,
    true
  from recent_sources;

  return next v_session;
end;
$$;


create function public.careline_get_session_memory(
  p_patient_id uuid,
  p_session_id uuid
)
returns table (
  "position" integer,
  source_session_id uuid,
  summary text
)
language sql
stable
security invoker
set search_path = ''
as $$
  select
    context_source.position,
    context_source.source_session_id,
    source.summary
  from public.chat_session_context_sources as context_source
  join public.chat_sessions as source
    on source.id = context_source.source_session_id
   and source.patient_id = context_source.patient_id
  where context_source.session_id = p_session_id
    and context_source.patient_id = p_patient_id
    and context_source.included_summary = true
    and source.summary is not null
    and exists (
      select 1
      from public.chat_sessions as target
      where target.id = p_session_id
        and target.patient_id = p_patient_id
    )
  order by context_source.position;
$$;

revoke all on function public.careline_create_chat_session_with_context(
  uuid, text, text, text, integer
) from public, anon, authenticated;
revoke all on function public.careline_get_session_memory(uuid, uuid)
  from public, anon, authenticated;

grant execute on function public.careline_create_chat_session_with_context(
  uuid, text, text, text, integer
) to service_role;
grant execute on function public.careline_get_session_memory(uuid, uuid)
  to service_role;
