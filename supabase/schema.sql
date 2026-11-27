-- ═══════════════════════════════════════════════════════════════
-- WorkClone DB 스키마 (Supabase / PostgreSQL)
-- 실행: Supabase 대시보드 → SQL Editor → 이 파일 내용 전체 붙여 넣기 → Run
-- 여러 번 실행해도 안전하도록 IF NOT EXISTS / OR REPLACE 를 사용합니다.
-- ═══════════════════════════════════════════════════════════════

-- ───────── 부서 ─────────
create table if not exists public.departments (
  id          smallint generated always as identity primary key,
  code        text not null unique,                  -- 파일 저장 폴더 등에 쓰는 영문 코드
  name        text not null unique,
  created_at  timestamptz not null default now()
);

insert into public.departments (code, name) values
  ('ACC', '회계팀'), ('LOG', '물류팀'), ('HR', '인사팀'), ('PR', '홍보팀')
on conflict (code) do nothing;

-- ───────── 직원 (로그인 계정 auth.users 와 1:1) ─────────
-- 비밀번호는 이 테이블에 두지 않습니다. Supabase Auth가 암호화해 따로 보관합니다.
create table if not exists public.employees (
  id             uuid primary key references auth.users(id) on delete cascade,
  emp_no         text not null unique check (emp_no ~ '^[0-9]{4,8}$'),   -- 사번 = 로그인 ID
  name           text not null,
  department_id  smallint not null references public.departments(id),
  position       text not null default '사원',                         -- 직급
  email          text not null unique,
  extension      text,                                                   -- 내선번호
  hire_date      date not null,                                          -- 입사일
  resign_date    date,                                                   -- 퇴사일 (재직 중이면 비움)
  status         text not null default '재직' check (status in ('재직', '휴직', '퇴사')),
  role           text not null default 'member' check (role in ('member', 'admin')),
  created_at     timestamptz not null default now(),                     -- 생성일
  updated_at     timestamptz not null default now(),
  check (resign_date is null or resign_date >= hire_date),
  check ((status = '퇴사') = (resign_date is not null))                  -- 퇴사 상태와 퇴사일은 함께
);
create index if not exists employees_department_idx on public.employees (department_id);

-- ───────── 자동화 (직원이 만든 레시피) ─────────
create table if not exists public.automations (
  id             uuid primary key default gen_random_uuid(),
  owner_id       uuid not null references public.employees(id),
  department_id  smallint not null references public.departments(id),
  name           text not null,
  summary        text not null default '',
  kind           text not null default 'excel' check (kind in ('excel', 'pdf', 'docx', 'pptx')),
  recipe         jsonb not null,                       -- 처리 단계 (AI가 만들고 검증한 레시피)
  source         jsonb not null default '{}',          -- 만들 때 쓴 파일·설명·실행값
  match_rate     numeric(5, 4),                        -- 업무 후 파일과 일치율 (0~1)
  verified       boolean not null default false,
  version        int not null default 1,
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists automations_department_idx on public.automations (department_id, created_at desc);

-- ───────── 실행 이력 ─────────
create table if not exists public.automation_runs (
  id               uuid primary key default gen_random_uuid(),
  automation_id    uuid references public.automations(id) on delete set null,
  automation_name  text not null,                      -- 자동화가 지워져도 이력에 이름이 남도록
  run_by           uuid not null references public.employees(id),
  department_id    smallint not null references public.departments(id),
  status           text not null check (status in ('success', 'failed')),
  input_files      jsonb not null default '[]',
  output_files     jsonb not null default '[]',        -- Storage 경로
  params           jsonb not null default '{}',
  row_count        int,
  error            text,
  created_at       timestamptz not null default now()
);
create index if not exists runs_department_idx on public.automation_runs (department_id, created_at desc);

-- ───────── AI 사용 기록 (1인당 하루 사용 횟수 제한용) ─────────
create table if not exists public.ai_usage (
  id           bigint generated always as identity primary key,
  employee_id  uuid not null references public.employees(id),
  kind         text not null,                          -- build(자동화 만들기) / payroll(급여 정산)
  created_at   timestamptz not null default now()
);
create index if not exists ai_usage_employee_idx on public.ai_usage (employee_id, created_at desc);

-- ───────── updated_at 자동 갱신 ─────────
create or replace function public.set_updated_at() returns trigger
language plpgsql as $$ begin new.updated_at = now(); return new; end $$;

drop trigger if exists employees_updated_at on public.employees;
create trigger employees_updated_at before update on public.employees
  for each row execute function public.set_updated_at();
drop trigger if exists automations_updated_at on public.automations;
create trigger automations_updated_at before update on public.automations
  for each row execute function public.set_updated_at();

-- ───────── 권한 (RLS): 같은 부서끼리만 자동화·이력을 봄 ─────────
-- 로그인한 직원의 부서 (퇴사자는 없음 → 아무것도 못 봄)
create or replace function public.my_department_id() returns smallint
language sql stable security definer set search_path = public as $$
  select department_id from public.employees where id = auth.uid() and status <> '퇴사'
$$;

alter table public.departments     enable row level security;
alter table public.employees       enable row level security;
alter table public.automations     enable row level security;
alter table public.automation_runs enable row level security;
alter table public.ai_usage        enable row level security;

drop policy if exists "부서 목록 조회" on public.departments;
create policy "부서 목록 조회" on public.departments for select to authenticated using (true);

drop policy if exists "직원 목록 조회" on public.employees;
create policy "직원 목록 조회" on public.employees for select to authenticated using (true);

drop policy if exists "같은 부서 자동화 조회" on public.automations;
create policy "같은 부서 자동화 조회" on public.automations for select to authenticated
  using (department_id = public.my_department_id());
drop policy if exists "내 자동화 만들기" on public.automations;
create policy "내 자동화 만들기" on public.automations for insert to authenticated
  with check (owner_id = auth.uid() and department_id = public.my_department_id());
drop policy if exists "내 자동화 수정" on public.automations;
create policy "내 자동화 수정" on public.automations for update to authenticated
  using (owner_id = auth.uid()) with check (owner_id = auth.uid() and department_id = public.my_department_id());
drop policy if exists "내 자동화 삭제" on public.automations;
create policy "내 자동화 삭제" on public.automations for delete to authenticated
  using (owner_id = auth.uid());

drop policy if exists "같은 부서 실행 이력 조회" on public.automation_runs;
create policy "같은 부서 실행 이력 조회" on public.automation_runs for select to authenticated
  using (department_id = public.my_department_id());
drop policy if exists "실행 이력 기록" on public.automation_runs;
create policy "실행 이력 기록" on public.automation_runs for insert to authenticated
  with check (run_by = auth.uid() and department_id = public.my_department_id());

drop policy if exists "내 AI 사용 기록 조회" on public.ai_usage;
create policy "내 AI 사용 기록 조회" on public.ai_usage for select to authenticated
  using (employee_id = auth.uid());
drop policy if exists "내 AI 사용 기록 추가" on public.ai_usage;
create policy "내 AI 사용 기록 추가" on public.ai_usage for insert to authenticated
  with check (employee_id = auth.uid());

-- ───────── 파일 저장소: 부서 코드 폴더(ACC/..., LOG/...)별로 같은 부서만 접근 ─────────
insert into storage.buckets (id, name, public)
values ('workfiles', 'workfiles', false)
on conflict (id) do nothing;

drop policy if exists "같은 부서 파일 조회" on storage.objects;
create policy "같은 부서 파일 조회" on storage.objects for select to authenticated
  using (bucket_id = 'workfiles' and (storage.foldername(name))[1] =
         (select code from public.departments where id = public.my_department_id()));
drop policy if exists "같은 부서 파일 올리기" on storage.objects;
create policy "같은 부서 파일 올리기" on storage.objects for insert to authenticated
  with check (bucket_id = 'workfiles' and (storage.foldername(name))[1] =
              (select code from public.departments where id = public.my_department_id()));

-- ───────── Data API 접근 권한 ─────────
-- 프로젝트를 만들 때 'Grant API access to new tables automatically'를 끄셨어도 동작하도록 직접 부여합니다.
-- (실제로 누가 어떤 행을 볼지는 위의 RLS 규칙이 결정합니다)
grant usage on schema public to anon, authenticated, service_role;
grant select on public.departments, public.employees to authenticated;
grant select, insert, update, delete on public.automations to authenticated;
grant select, insert on public.automation_runs, public.ai_usage to authenticated;
grant all on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to authenticated, service_role;
grant execute on function public.my_department_id() to authenticated;
