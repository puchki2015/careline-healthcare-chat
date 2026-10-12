create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger patients_set_updated_at
  before update on public.patients
  for each row execute function public.set_updated_at();