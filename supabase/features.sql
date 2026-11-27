-- ═══════════════════════════════════════════════════════════════
-- 팀원 기능용 테이블 (회의 분석 · 회의록 검색 · 오늘 브리핑 · 주간 업무보고 · 업무 메일)
-- schema.sql을 먼저 실행한 뒤 Supabase SQL Editor에서 실행하세요. 여러 번 실행해도 됩니다.
-- 규칙은 schema.sql과 같습니다: 같은 부서 직원만 보고, 수정·삭제는 만든 사람만.
-- ═══════════════════════════════════════════════════════════════

-- ───────── 회의록 (회의 분석이 저장 → 회의록 검색·오늘 브리핑이 읽음) ─────────
create table if not exists public.meetings (
  id             uuid primary key default gen_random_uuid(),
  owner_id       uuid not null references public.employees(id) on delete cascade default auth.uid(),
  department_id  smallint not null references public.departments(id) default public.my_department_id(),
  title          text not null,                         -- 예: 11월 4주차 물류팀 주간 회의
  held_at        date not null default current_date,    -- 회의 날짜
  participants   text[] not null default '{}',          -- 참석자 이름
  transcript     text not null default '',              -- 회의록 원문 (올린 글 그대로)
  summary        text,                                  -- AI 요약 (3~5줄)
  decisions      jsonb not null default '[]',           -- 결정 사항: ["…", "…"]
  action_items   jsonb not null default '[]',           -- 할 일: [{"task":"…","assignee":"이름","due":"2026-12-01","done":false}]
  tags           text[] not null default '{}',          -- 검색용 키워드
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists meetings_dept_idx on public.meetings (department_id, held_at desc);

-- ───────── 업무 메일 (가상 메일함) ─────────
create table if not exists public.mails (
  id             uuid primary key default gen_random_uuid(),
  recipient_id   uuid not null references public.employees(id) on delete cascade default auth.uid(),
  department_id  smallint not null references public.departments(id) default public.my_department_id(),
  from_name      text not null,                         -- 보낸 사람 (예: 대성유통 김민수)
  from_email     text not null,
  subject        text not null,
  body           text not null default '',
  attachments    jsonb not null default '[]',           -- [{"name":"거래명세서.pdf","path":"LOG/mail/…"}] (파일은 workfiles 저장소)
  received_at    timestamptz not null default now(),
  is_read        boolean not null default false,
  category       text,                                  -- AI 분류 (예: 거래명세서, 증명서 요청, 회의 일정)
  automation_id  uuid references public.automations(id) on delete set null,  -- 연결된 자동화
  reply_draft    text,                                  -- AI 답장 초안
  created_at     timestamptz not null default now()
);
create index if not exists mails_recipient_idx on public.mails (recipient_id, received_at desc);

-- ───────── 주간 업무보고 (초안 저장) ─────────
create table if not exists public.weekly_reports (
  id             uuid primary key default gen_random_uuid(),
  owner_id       uuid not null references public.employees(id) on delete cascade default auth.uid(),
  department_id  smallint not null references public.departments(id) default public.my_department_id(),
  week_start     date not null,                         -- 그 주 월요일
  content        text not null default '',              -- 보고서 본문 (마크다운)
  sources        jsonb not null default '{}',           -- 근거: {"runs":[…], "meetings":[…], "mails":[…]}
  status         text not null default '초안' check (status in ('초안', '제출')),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (owner_id, week_start)
);

drop trigger if exists meetings_updated_at on public.meetings;
create trigger meetings_updated_at before update on public.meetings for each row execute function public.set_updated_at();
drop trigger if exists weekly_reports_updated_at on public.weekly_reports;
create trigger weekly_reports_updated_at before update on public.weekly_reports for each row execute function public.set_updated_at();

alter table public.meetings       enable row level security;
alter table public.mails          enable row level security;
alter table public.weekly_reports enable row level security;

-- 회의록: 같은 부서가 보고, 만든 사람만 고치고 지움
drop policy if exists "같은 부서 회의록 조회" on public.meetings;
create policy "같은 부서 회의록 조회" on public.meetings for select to authenticated using (department_id = public.my_department_id());
drop policy if exists "회의록 등록" on public.meetings;
create policy "회의록 등록" on public.meetings for insert to authenticated
  with check (owner_id = auth.uid() and department_id = public.my_department_id());
drop policy if exists "내 회의록 수정" on public.meetings;
create policy "내 회의록 수정" on public.meetings for update to authenticated
  using (owner_id = auth.uid()) with check (owner_id = auth.uid() and department_id = public.my_department_id());
drop policy if exists "내 회의록 삭제" on public.meetings;
create policy "내 회의록 삭제" on public.meetings for delete to authenticated using (owner_id = auth.uid());

-- 메일: 받는 사람 본인만 (가상 메일 서버가 본인 메일함에 넣는 것은 허용)
drop policy if exists "내 메일 조회" on public.mails;
create policy "내 메일 조회" on public.mails for select to authenticated using (recipient_id = auth.uid());
drop policy if exists "내 메일함에 넣기" on public.mails;
create policy "내 메일함에 넣기" on public.mails for insert to authenticated
  with check (recipient_id = auth.uid() and department_id = public.my_department_id());
drop policy if exists "내 메일 수정" on public.mails;
create policy "내 메일 수정" on public.mails for update to authenticated using (recipient_id = auth.uid()) with check (recipient_id = auth.uid());
drop policy if exists "내 메일 삭제" on public.mails;
create policy "내 메일 삭제" on public.mails for delete to authenticated using (recipient_id = auth.uid());

-- 주간 보고: 같은 부서가 보고(팀장 확인용), 쓴 사람만 고침
drop policy if exists "같은 부서 주간보고 조회" on public.weekly_reports;
create policy "같은 부서 주간보고 조회" on public.weekly_reports for select to authenticated using (department_id = public.my_department_id());
drop policy if exists "주간보고 작성" on public.weekly_reports;
create policy "주간보고 작성" on public.weekly_reports for insert to authenticated
  with check (owner_id = auth.uid() and department_id = public.my_department_id());
drop policy if exists "내 주간보고 수정" on public.weekly_reports;
create policy "내 주간보고 수정" on public.weekly_reports for update to authenticated
  using (owner_id = auth.uid()) with check (owner_id = auth.uid());
drop policy if exists "내 주간보고 삭제" on public.weekly_reports;
create policy "내 주간보고 삭제" on public.weekly_reports for delete to authenticated using (owner_id = auth.uid());

grant select, insert, update, delete on public.meetings, public.mails, public.weekly_reports to authenticated;

-- ═══════════════════════════════════════════════════════════════
-- 업무 메일 추가분 (보내기 · 보낸 메일함 · 첨부파일 · 자동화 실행 기록)
-- ═══════════════════════════════════════════════════════════════
alter table public.mails add column if not exists sender_id  uuid references public.employees(id) on delete set null;
alter table public.mails add column if not exists run_id     text;          -- 첨부파일로 실행한 자동화 실행 기록
alter table public.mails add column if not exists summary    text;          -- AI 한 줄 요약
alter table public.mails add column if not exists replied_at timestamptz;
alter table public.mails add column if not exists to_name    text;          -- 받는 사람 표시용 (보낸 메일함)
create index if not exists mails_sender_idx on public.mails (sender_id, received_at desc);

-- 받은 사람과 보낸 사람이 볼 수 있음
drop policy if exists "내 메일 조회" on public.mails;
create policy "내 메일 조회" on public.mails for select to authenticated
  using (recipient_id = auth.uid() or sender_id = auth.uid());

-- 다른 직원에게 보내기: 직접 넣기는 막혀 있으므로(본인 메일함만) 이 함수로만 보냄
create or replace function public.send_mail(p_to_emp_no text, p_subject text, p_body text, p_attachments jsonb default '[]')
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_to uuid; v_dept smallint; v_to_name text; v_name text; v_email text; v_from_dept text; v_id uuid;
begin
  select e.id, e.department_id, e.name || ' (' || d.name || ')' into v_to, v_dept, v_to_name
    from public.employees e join public.departments d on d.id = e.department_id
   where e.emp_no = p_to_emp_no and e.status <> '퇴사';
  if v_to is null then raise exception '받는 사람(사번 %)을 찾을 수 없습니다', p_to_emp_no; end if;
  select e.name, e.email, d.name into v_name, v_email, v_from_dept
    from public.employees e join public.departments d on d.id = e.department_id
   where e.id = auth.uid() and e.status <> '퇴사';
  if v_name is null then raise exception '보내는 사람 정보를 찾을 수 없습니다'; end if;
  insert into public.mails (recipient_id, department_id, sender_id, to_name, from_name, from_email, subject, body, attachments)
  values (v_to, v_dept, auth.uid(), v_to_name, v_name || ' (' || v_from_dept || ')', v_email, left(p_subject, 300), left(p_body, 20000),
          coalesce(p_attachments, '[]'::jsonb))
  returning id into v_id;
  return v_id;
end $$;
revoke all on function public.send_mail(text, text, text, jsonb) from public;
grant execute on function public.send_mail(text, text, text, jsonb) to authenticated;

-- 메일 첨부파일: 부서가 달라도 주고받을 수 있게 MAIL/ 폴더 (경로에 임의 번호가 들어가 남이 짐작할 수 없음)
drop policy if exists "메일 첨부 올리기" on storage.objects;
create policy "메일 첨부 올리기" on storage.objects for insert to authenticated
  with check (bucket_id = 'workfiles' and (storage.foldername(name))[1] = 'MAIL');
drop policy if exists "메일 첨부 보기" on storage.objects;
create policy "메일 첨부 보기" on storage.objects for select to authenticated
  using (bucket_id = 'workfiles' and (storage.foldername(name))[1] = 'MAIL');
