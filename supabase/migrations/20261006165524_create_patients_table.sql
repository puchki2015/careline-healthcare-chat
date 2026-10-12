create table public.patients (
  id                uuid primary key references auth.users (id) on delete cascade,
  email             text,
  full_name         text,
  phone             text,
  date_of_birth     date,
  gender            text,
  address           jsonb not null default '{}'::jsonb,
  emergency_contact jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  constraint patients_phone_format
    check (phone is null or phone ~ '^\+[1-9][0-9]{7,14}$'),
  constraint patients_gender_valid
    check (gender is null or gender in
      ('female', 'male', 'non_binary', 'other', 'prefer_not_to_say'))
);
comment on table public.patients is 'Patient profile, one row per auth user.';