-- ═══════════════════════════════════════════════════════════════
-- 권한 규칙(RLS) 검사 — SQL Editor에서 실행하면 결과 표가 나옵니다.
-- 회계팀(1001)과 물류팀(2001) 직원 권한으로 바꿔 가며 확인하고, 검사용 데이터는 마지막에 지웁니다.
-- ═══════════════════════════════════════════════════════════════
create temp table if not exists rls_check (no int, 항목 text, 결과 text);
truncate rls_check;

do $$
declare
  acc uuid; logi uuid; acc_dept smallint; log_dept smallint;
  test_id uuid; n int; ok boolean;
  results text[] := '{}';
begin
  select id, department_id into acc, acc_dept from public.employees where emp_no = '1001';
  select id, department_id into logi, log_dept from public.employees where emp_no = '2001';

  -- ① 회계팀 직원(1001)으로 전환
  perform set_config('request.jwt.claims', json_build_object('sub', acc, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  insert into public.automations (owner_id, department_id, name, recipe)
    values (acc, acc_dept, '[RLS 검사] 회계 자동화', '{}') returning id into test_id;
  results := array_append(results, ('회계팀 직원이 회계팀 자동화 저장|' || case when test_id is not null then 'PASS' else 'FAIL' end)::text);

  begin
    insert into public.automations (owner_id, department_id, name, recipe) values (acc, log_dept, '[RLS 검사] 다른 부서', '{}');
    results := array_append(results, '회계팀 직원이 물류팀으로 저장 → 차단|FAIL'::text);
  exception when others then
    results := array_append(results, '회계팀 직원이 물류팀으로 저장 → 차단|PASS'::text);
  end;

  select count(*) into n from public.automations where id = test_id;
  results := array_append(results, ('같은 부서(회계팀)는 보임|' || case when n = 1 then 'PASS' else 'FAIL' end)::text);
  execute 'reset role';

  -- ② 물류팀 직원(2001)으로 전환
  perform set_config('request.jwt.claims', json_build_object('sub', logi, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into n from public.automations where id = test_id;
  results := array_append(results, ('다른 부서(물류팀)는 안 보임|' || case when n = 0 then 'PASS' else 'FAIL' end)::text);
  delete from public.automations where id = test_id;
  execute 'reset role';
  select count(*) into n from public.automations where id = test_id;
  results := array_append(results, ('물류팀 직원은 회계팀 자동화 삭제 불가|' || case when n = 1 then 'PASS' else 'FAIL' end)::text);

  -- ③ 파일 폴더 규칙 (물류팀 직원이 회계팀 폴더 ACC/ 에 쓰기)
  perform set_config('request.jwt.claims', json_build_object('sub', logi, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  begin
    insert into storage.objects (bucket_id, name, owner) values ('workfiles', 'ACC/rls-check/hack.txt', logi);
    results := array_append(results, '물류팀 직원이 회계팀 폴더에 파일 쓰기 → 차단|FAIL'::text);
  exception when others then
    results := array_append(results, '물류팀 직원이 회계팀 폴더에 파일 쓰기 → 차단|PASS'::text);
  end;
  execute 'reset role';

  -- ④ 퇴사자는 아무것도 못 봄 (물류팀 자동화를 하나 만들고, 2001을 잠시 퇴사 처리했다가 되돌림)
  insert into public.automations (owner_id, department_id, name, recipe)
    values (logi, log_dept, '[RLS 검사] 물류 자동화', '{}') returning id into test_id;
  perform set_config('request.jwt.claims', json_build_object('sub', logi, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into n from public.automations where id = test_id;
  execute 'reset role';
  results := array_append(results, ('재직 중인 물류팀 직원은 물류팀 자동화 보임|' || case when n = 1 then 'PASS' else 'FAIL' end)::text);
  update public.employees set status = '퇴사', resign_date = hire_date where id = logi;
  perform set_config('request.jwt.claims', json_build_object('sub', logi, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into n from public.automations where id = test_id;
  execute 'reset role';
  update public.employees set status = '재직', resign_date = null where id = logi;
  results := array_append(results, ('퇴사 처리된 직원은 자동화 조회 0건|' || case when n = 0 then 'PASS' else 'FAIL' end)::text);

  -- 정리
  delete from public.automations where name like '[RLS 검사]%';
  -- 파일 테이블은 SQL로 직접 지울 수 없음 (Supabase 보호 기능). 차단이 정상이면 애초에 파일이 생기지 않음
  begin
    delete from storage.objects where bucket_id = 'workfiles' and name like 'ACC/rls-check/%';
  exception when others then null;
  end;

  for i in 1 .. array_length(results, 1) loop
    insert into rls_check values (i, split_part(results[i], '|', 1), split_part(results[i], '|', 2));
  end loop;
end $$;

select no as "번호", 항목, 결과 from rls_check order by no;
