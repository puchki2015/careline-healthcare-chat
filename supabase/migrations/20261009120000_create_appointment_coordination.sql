-- Appointment Coordinator foundation.
--
-- The language model never writes these tables. It can only propose an action;
-- the backend validates it, stores a one-time confirmation, and calls one of
-- the transactional functions below after the patient explicitly approves.

create table public.doctor_profiles (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid unique references auth.users (id) on delete set null,
  display_name  text not null check (char_length(trim(display_name)) between 2 and 120),
  specialty     text not null check (char_length(trim(specialty)) between 2 and 120),
  timezone      text not null default 'UTC',
  is_active     boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table public.provider_availability (
  id             uuid primary key default gen_random_uuid(),
  doctor_id      uuid not null references public.doctor_profiles (id) on delete cascade,
  starts_at      timestamptz not null,
  ends_at        timestamptz not null,
  status         text not null default 'available',
  location_type  text not null default 'in_person',
  location       text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint provider_availability_time_valid check (ends_at > starts_at),
  constraint provider_availability_status_valid
    check (status in ('available', 'booked', 'blocked')),
  constraint provider_availability_location_type_valid
    check (location_type in ('in_person', 'video', 'phone')),
  constraint provider_availability_unique_slot unique (doctor_id, starts_at, ends_at)
);

create table public.appointments (
  id                    uuid primary key default gen_random_uuid(),
  patient_id            uuid not null references public.patients (id) on delete restrict,
  doctor_id             uuid not null references public.doctor_profiles (id) on delete restrict,
  availability_id       uuid references public.provider_availability (id) on delete restrict,
  starts_at              timestamptz not null,
  ends_at                timestamptz not null,
  status                 text not null default 'scheduled',
  reason                 text,
  cancellation_reason    text,
  created_by             uuid not null references auth.users (id) on delete restrict,
  created_by_role        text not null,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint appointments_time_valid check (ends_at > starts_at),
  constraint appointments_status_valid
    check (status in ('scheduled', 'cancelled', 'completed', 'no_show')),
  constraint appointments_actor_role_valid
    check (created_by_role in ('patient', 'admin', 'doctor')),
  constraint appointments_reason_length
    check (reason is null or char_length(reason) <= 1000),
  constraint appointments_cancellation_reason_length
    check (cancellation_reason is null or char_length(cancellation_reason) <= 1000)
);

create table public.appointment_status_history (
  id                uuid primary key default gen_random_uuid(),
  appointment_id    uuid not null references public.appointments (id) on delete cascade,
  from_status       text,
  to_status         text not null,
  changed_by        uuid not null references auth.users (id) on delete restrict,
  changed_by_role   text not null,
  reason            text,
  created_at        timestamptz not null default now(),
  constraint appointment_history_from_status_valid check (
    from_status is null or from_status in ('scheduled', 'cancelled', 'completed', 'no_show')
  ),
  constraint appointment_history_to_status_valid
    check (to_status in ('scheduled', 'cancelled', 'completed', 'no_show', 'rescheduled')),
  constraint appointment_history_actor_role_valid
    check (changed_by_role in ('patient', 'admin', 'doctor')),
  constraint appointment_history_reason_length
    check (reason is null or char_length(reason) <= 1000)
);

create index appointments_patient_time_idx
  on public.appointments (patient_id, starts_at desc);
-- A cancelled appointment keeps its historical slot reference, while this
-- partial unique index still guarantees only one live booking per slot.
create unique index appointments_one_scheduled_booking_per_slot_idx
  on public.appointments (availability_id)
  where status = 'scheduled';
create index provider_availability_search_idx
  on public.provider_availability (status, starts_at, doctor_id);
create index appointment_status_history_appointment_idx
  on public.appointment_status_history (appointment_id, created_at);

comment on table public.doctor_profiles is
  'Care providers whose availability can be searched by the appointment agent.';
comment on table public.provider_availability is
  'Authoritative provider time slots; only available rows may be booked.';
comment on table public.appointments is
  'Patient appointments created only after deterministic validation and confirmation.';
comment on table public.appointment_status_history is
  'Immutable audit trail for appointment lifecycle changes.';

alter table public.doctor_profiles enable row level security;
alter table public.provider_availability enable row level security;
alter table public.appointments enable row level security;
alter table public.appointment_status_history enable row level security;

revoke all on public.doctor_profiles from anon, authenticated;
revoke all on public.provider_availability from anon, authenticated;
revoke all on public.appointments from anon, authenticated;
revoke all on public.appointment_status_history from anon, authenticated;
grant select, insert, update, delete on public.doctor_profiles to service_role;
grant select, insert, update, delete on public.provider_availability to service_role;
grant select, insert, update, delete on public.appointments to service_role;
grant select, insert, update, delete on public.appointment_status_history to service_role;

-- Add appointment proposals to the same durable/auditable pending-action table
-- used by profile changes and administrator invitations.
alter table public.chat_pending_actions drop constraint chat_pending_actions_type_valid;
alter table public.chat_pending_actions add constraint chat_pending_actions_type_valid check (
  action_type in (
    'update_profile', 'create_patient', 'delete_account',
    'book_appointment', 'reschedule_appointment', 'cancel_appointment'
  )
);

create function public.careline_book_appointment(
  p_patient_id uuid,
  p_slot_id uuid,
  p_reason text,
  p_actor_id uuid,
  p_actor_role text
)
returns table (outcome text, appointment_id uuid)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_slot public.provider_availability%rowtype;
  v_appointment_id uuid;
begin
  select * into v_slot from public.provider_availability
  where id = p_slot_id for update;
  if not found or v_slot.status <> 'available' or v_slot.starts_at <= now() then
    return query select 'slot_unavailable'::text, null::uuid;
    return;
  end if;
  insert into public.appointments (
    patient_id, doctor_id, availability_id, starts_at, ends_at, reason,
    created_by, created_by_role
  ) values (
    p_patient_id, v_slot.doctor_id, v_slot.id, v_slot.starts_at, v_slot.ends_at,
    nullif(trim(p_reason), ''), p_actor_id, p_actor_role
  ) returning id into v_appointment_id;
  update public.provider_availability set status = 'booked', updated_at = now()
  where id = v_slot.id;
  insert into public.appointment_status_history (
    appointment_id, from_status, to_status, changed_by, changed_by_role, reason
  ) values (v_appointment_id, null, 'scheduled', p_actor_id, p_actor_role, 'Booked');
  return query select 'booked'::text, v_appointment_id;
end;
$$;

create function public.careline_reschedule_appointment(
  p_patient_id uuid,
  p_appointment_id uuid,
  p_new_slot_id uuid,
  p_actor_id uuid,
  p_actor_role text
)
returns table (outcome text, appointment_id uuid)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_appointment public.appointments%rowtype;
  v_slot public.provider_availability%rowtype;
begin
  select * into v_appointment from public.appointments
  where id = p_appointment_id and patient_id = p_patient_id for update;
  if not found or v_appointment.status <> 'scheduled' then
    return query select 'appointment_unavailable'::text, null::uuid;
    return;
  end if;
  select * into v_slot from public.provider_availability
  where id = p_new_slot_id for update;
  if not found or v_slot.status <> 'available' or v_slot.starts_at <= now() then
    return query select 'slot_unavailable'::text, null::uuid;
    return;
  end if;
  update public.provider_availability set status = 'available', updated_at = now()
  where id = v_appointment.availability_id;
  update public.provider_availability set status = 'booked', updated_at = now()
  where id = v_slot.id;
  update public.appointments set
    doctor_id = v_slot.doctor_id, availability_id = v_slot.id,
    starts_at = v_slot.starts_at, ends_at = v_slot.ends_at, updated_at = now()
  where id = v_appointment.id;
  insert into public.appointment_status_history (
    appointment_id, from_status, to_status, changed_by, changed_by_role, reason
  ) values (
    v_appointment.id, 'scheduled', 'rescheduled', p_actor_id, p_actor_role,
    'Moved to a new availability slot'
  );
  return query select 'rescheduled'::text, v_appointment.id;
end;
$$;

create function public.careline_cancel_appointment(
  p_patient_id uuid,
  p_appointment_id uuid,
  p_reason text,
  p_actor_id uuid,
  p_actor_role text
)
returns table (outcome text, appointment_id uuid)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_appointment public.appointments%rowtype;
begin
  select * into v_appointment from public.appointments
  where id = p_appointment_id and patient_id = p_patient_id for update;
  if not found or v_appointment.status <> 'scheduled' then
    return query select 'appointment_unavailable'::text, null::uuid;
    return;
  end if;
  update public.appointments set
    status = 'cancelled', cancellation_reason = nullif(trim(p_reason), ''), updated_at = now()
  where id = v_appointment.id;
  update public.provider_availability set status = 'available', updated_at = now()
  where id = v_appointment.availability_id;
  insert into public.appointment_status_history (
    appointment_id, from_status, to_status, changed_by, changed_by_role, reason
  ) values (
    v_appointment.id, 'scheduled', 'cancelled', p_actor_id, p_actor_role,
    nullif(trim(p_reason), '')
  );
  return query select 'cancelled'::text, v_appointment.id;
end;
$$;

revoke all on function public.careline_book_appointment(uuid, uuid, text, uuid, text)
  from public, anon, authenticated;
revoke all on function public.careline_reschedule_appointment(uuid, uuid, uuid, uuid, text)
  from public, anon, authenticated;
revoke all on function public.careline_cancel_appointment(uuid, uuid, text, uuid, text)
  from public, anon, authenticated;
grant execute on function public.careline_book_appointment(uuid, uuid, text, uuid, text)
  to service_role;
grant execute on function public.careline_reschedule_appointment(uuid, uuid, uuid, uuid, text)
  to service_role;
grant execute on function public.careline_cancel_appointment(uuid, uuid, text, uuid, text)
  to service_role;
