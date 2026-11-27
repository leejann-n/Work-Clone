/* 내 자동화(목록·실행) · 실행 이력 · 홈 대시보드(템플릿, 현황, 최근 실행) */
(function () {
  'use strict';

  var U = window.WCU;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var icons = function (r) { window.WC.renderIcons(r); };
  var autos = [], current = null, runFiles = [];

  // data-go="화면" 버튼 공통 처리
  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-go]');
    if (el) { e.preventDefault(); window.WC.navigate(el.dataset.go); }
  });

  document.addEventListener('wc:page', function (e) {
    if (e.detail === 'my') loadAutos();
    if (e.detail === 'history') loadHistory();
    if (e.detail === 'home') loadHome();
  });

  /* ───────── 내 자동화 ───────── */
  function loadAutos() {
    U.json('/api/automations').then(function (list) {
      autos = list;
      var emp = window.WCAuth.employee();
      $('#my-count').textContent = (emp ? emp.department + ' 공유 · ' : '') + list.length + '개';
      var ul = $('#my-list');
      if (!list.length) {
        ul.innerHTML = '<li class="empty">아직 저장된 자동화가 없습니다.<br>홈에서 업무 예시를 올려 첫 자동화를 만들어 보세요.</li>';
        $('#my-detail').innerHTML = '<div class="empty">자동화를 만들면 여기서 새 파일로 바로 실행할 수 있습니다.</div>';
        return;
      }
      ul.innerHTML = list.map(function (a) {
        return '<li data-id="' + a.id + '"' + (current && current.id === a.id ? ' class="on"' : '') + '><span class="f-ic"><i data-icon="file-spreadsheet" data-size="16"></i></span>' +
          '<div><b>' + U.esc(a.name) + '</b><small>' + (a.owner_name ? U.esc(a.owner_name) + ' · ' : '') + a.steps.length + '단계 · 실행 ' + a.run_count + '회' +
          (a.last_run ? ' · ' + U.timeAgo(a.last_run) : '') + '</small></div>' +
          (a.source && a.source.verified ? '<span class="badge ok">검증됨</span>' : '<span class="badge warn">미검증</span>') + '</li>';
      }).join('');
      icons(ul);
      $$('li[data-id]', ul).forEach(function (li) { li.addEventListener('click', function () { select(li.dataset.id); }); });
      select(current && autos.some(function (a) { return a.id === current.id; }) ? current.id : list[0].id);
    }).catch(function (e) { $('#my-list').innerHTML = '<li class="empty">' + U.esc(e.message) + '</li>'; });
  }

  function select(id) {
    current = autos.find(function (a) { return a.id === id; });
    runFiles = [];
    $$('#my-list li').forEach(function (li) { li.classList.toggle('on', li.dataset.id === id); });
    var a = current;
    var params = (a.params || []).map(function (p) {
      return '<label class="prm"><span>' + U.esc(p.name) + ' <small>' + U.esc(p.description || '') + '</small></span>' +
        '<input data-param="' + U.esc(p.name) + '" placeholder="비우면 파일 이름에서 자동 (예시값 ' + U.esc(p.default) + ')" /></label>';
    }).join('');
    $('#my-detail').innerHTML =
      '<div class="md-head"><div><h3>' + U.esc(a.name) + '</h3><p>' + U.esc(a.summary) + '</p></div>' +
      (a.is_mine === false ? '<span class="owner-tag">만든 사람: ' + U.esc(a.owner_name) + '</span>' : '<button type="button" class="btn-ghost danger" id="my-del">삭제</button>') + '</div>' +
      '<div class="md-cols"><div class="md-steps"><h4>처리 단계</h4><ol class="step-list compact">' + a.steps.map(function (s) {
        return '<li><span class="st-no">' + s.step + '</span><div><b>' + U.esc(s.note || s.label) + '</b><p>' + U.esc(s.say) + '</p></div></li>';
      }).join('') + '</ol>' +
      '<p class="tbl-note">' + (a.source && a.source.match_rate != null ? '만들 때 업무 후 파일과 일치율 ' + (a.source.match_rate * 100).toFixed(1) + '% · ' : '') + '저장 ' + U.esc(a.created_at.slice(0, 10)) + '</p></div>' +
      '<div class="md-run"><h4>새 파일로 실행</h4>' +
      '<p class="tbl-note">필요한 입력: ' + a.inputs.map(function (i) { return '<b>' + U.esc(i.table) + '</b> (' + U.esc((i.columns || []).join(', ')) + ')'; }).join(' · ') + '</p>' +
      '<div class="slot run-drop" id="my-drop"><div class="slot-main"><span class="slot-ic"><i data-icon="file-up" data-size="20"></i></span>' +
      '<span class="slot-text"><strong>이번 달 업무 전 파일</strong><small>엑셀·CSV를 끌어다 놓거나 선택하세요</small></span>' +
      '<button type="button" class="btn-outline" id="my-pick"><i data-icon="upload" data-size="14"></i> 선택</button></div>' +
      '<div class="slot-files" id="my-files"></div><input type="file" id="my-input" multiple hidden accept=".xlsx,.xlsm,.csv,.pdf" /></div>' +
      params +
      '<button type="button" class="btn-primary" id="my-run" disabled><i data-icon="play" data-size="14" data-fill="currentColor"></i> 실행</button>' +
      '<div id="my-result"></div></div></div>';
    icons($('#my-detail'));
    var input = $('#my-input'), drop = $('#my-drop');
    $('#my-pick').addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () { addRunFiles(input.files); input.value = ''; });
    drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('dragging'); });
    drop.addEventListener('dragleave', function () { drop.classList.remove('dragging'); });
    drop.addEventListener('drop', function (e) { e.preventDefault(); drop.classList.remove('dragging'); addRunFiles(e.dataTransfer.files); });
    $('#my-run').addEventListener('click', run);
    if ($('#my-del')) $('#my-del').addEventListener('click', function () {
      if (!confirm('"' + a.name + '" 자동화를 삭제할까요? 실행 이력은 남습니다.')) return;
      U.json('/api/automations/' + a.id, { method: 'DELETE' }).then(function () { current = null; loadAutos(); });
    });
  }

  function addRunFiles(list) {
    Array.prototype.forEach.call(list, function (f) { if (!runFiles.some(function (x) { return x.name === f.name; })) runFiles.push(f); });
    $('#my-files').innerHTML = runFiles.map(function (f, i) {
      return '<span class="file-chip">' + U.esc(f.name) + '<button type="button" data-i="' + i + '" aria-label="제거">×</button></span>';
    }).join('');
    $('#my-drop').classList.toggle('filled', runFiles.length > 0);
    $$('#my-files button').forEach(function (b) {
      b.addEventListener('click', function () { runFiles.splice(+b.dataset.i, 1); addRunFiles([]); });
    });
    $('#my-run').disabled = !runFiles.length;
  }

  function run() {
    var btn = $('#my-run'), out = $('#my-result');
    btn.disabled = true;
    out.innerHTML = '<p class="tbl-note">실행 중…</p>';
    var fd = new FormData(), params = {};
    runFiles.forEach(function (f) { fd.append('files', f, f.name); });
    $$('[data-param]').forEach(function (i) { if (i.value.trim()) params[i.dataset.param] = i.value.trim(); });
    fd.append('params', JSON.stringify(params));
    U.json('/api/automations/' + current.id + '/run', { method: 'POST', body: fd }).then(function (r) {
      var st = { ok: ['✔', 'ok'], warn: ['⚠', 'warn'], error: ['✖', 'error'] };
      out.innerHTML =
        '<div class="run-ok"><b>✓ 실행 완료</b>' + (Object.keys(r.params || {}).length ? ' · 실행값: ' + U.esc(Object.keys(r.params).map(function (k) { return k + '=' + r.params[k]; }).join(', ')) : '') + '</div>' +
        '<ul class="check-list">' + r.checks.map(function (c) {
          return '<li><span class="badge ' + st[c.결과][1] + '">' + st[c.결과][0] + ' ' + ({ ok: '정상', warn: '확인', error: '오류' })[c.결과] + '</span><div><b>' + U.esc(c.항목) + '</b><p>' + U.esc(c.내용) + '</p></div></li>';
        }).join('') + '</ul>' +
        r.files.map(function (f) { return '<a class="btn-outline dl" href="' + U.esc(f.url) + '" download><i data-icon="download" data-size="14"></i> ' + U.esc(f.name) + '</a>'; }).join('') +
        Object.keys(r.previews).map(function (k) { return '<h4 class="pv-title">' + U.esc(k.replace('|', ' · ')) + '</h4>' + U.previewTable(r.previews[k]); }).join('');
      icons(out);
      loadHome();
      // 목록의 실행 횟수 갱신
      U.json('/api/automations').then(function (list) {
        autos = list;
        list.forEach(function (x) {
          var li = $('#my-list li[data-id="' + x.id + '"] small');
          if (li) li.textContent = x.steps.length + '단계 · 실행 ' + x.run_count + '회' + (x.last_run ? ' · ' + U.timeAgo(x.last_run) : '');
        });
      });
    }).catch(function (e) {
      out.innerHTML = '<div class="msg error">' + U.esc(e.message) + '</div>';
    }).then(function () { btn.disabled = !runFiles.length; });
  }

  /* ───────── 실행 이력 ───────── */
  function loadHistory() {
    U.json('/api/runs').then(function (runs) {
      $('#hist-count').textContent = runs.length + '건';
      $('#hist-body').innerHTML = runs.length ? runs.map(function (r) {
        var ok = r.status === 'success';
        return '<tr><td>' + U.esc(r.at.replace('T', ' ')) + '</td><td><b>' + U.esc(r.automation) + '</b></td><td>' + U.esc(r.run_by_name || '') +
          '</td><td>' + U.esc((r.inputs || []).join(', ')) +
          '</td><td><span class="badge ' + (ok ? 'ok' : 'error') + '">' + (ok ? '성공' : '실패') + '</span>' + (ok ? '' : '<small class="err-txt">' + U.esc(r.error || '') + '</small>') +
          '</td><td class="num">' + (ok ? U.fmt(r.rows) : '-') + '</td><td>' + (r.outputs || []).map(function (n) {
            return '<a href="/api/runs/' + r.id + '/files/' + encodeURIComponent(n) + '" download="' + U.esc(n) + '">' + U.esc(n) + '</a>';
          }).join('<br>') + '</td></tr>';
      }).join('') : '<tr><td colspan="7" class="empty-cell">아직 실행 기록이 없습니다.</td></tr>';
    }).catch(function (e) { $('#hist-body').innerHTML = '<tr><td colspan="7" class="empty-cell">' + U.esc(e.message) + '</td></tr>'; });
  }

  /* ───────── 홈: 추천 템플릿(예시) · 현황 · 최근 실행 ───────── */
  var samples = [];
  var retries = 0;
  // 서버(Render 무료)가 잠들어 있으면 첫 응답까지 30~60초 걸릴 수 있어, 안내 후 다시 시도
  function waiting() {
    var msg = '<li class="empty-li">서버를 깨우는 중입니다… (처음 접속 시 최대 1분)</li>';
    if (!samples.length) $('.tpl-list').innerHTML = msg;
    $('.run-list').innerHTML = msg;
    if (retries++ < 12) setTimeout(loadHome, 5000);
  }
  function loadHome() {
    if (location.protocol === 'file:') {
      $('.tpl-list').innerHTML = $('.run-list').innerHTML = '<li class="empty-li">서버에 연결하면 표시됩니다</li>';
      return;
    }
    if (!samples.length) {
      U.json('/api/samples/excel').then(function (list) {
        samples = list;
        $('.tpl-list').innerHTML = list.map(function (s, i) {
          return '<li><span class="tpl-ic cat-data"><i data-icon="file-spreadsheet" data-size="15"></i></span><span class="tpl-title">' + U.esc(s.title) +
            '</span><span class="tag cat-data">엑셀</span><button class="use-btn" data-sample-i="' + i + '"><i data-icon="play" data-size="10" data-fill="currentColor"></i> 사용</button></li>';
        }).join('');
        icons($('.tpl-list'));
        $$('[data-sample-i]').forEach(function (b) { b.addEventListener('click', function () { useSample(samples[+b.dataset.sampleI]); }); });
      }).catch(function () { /* stats 쪽에서 재시도 */ });
    }
    U.json('/api/stats').then(function (s) {
      var k = $$('.kpi strong');
      if (k.length >= 3) {
        k[0].innerHTML = s.automations + '<em>개</em>';
        k[1].innerHTML = s.runs + '<em>회</em>';
        k[2].innerHTML = s.success_rate == null ? '–' : s.success_rate + '<em>%</em>';
      }
      if (window.WC.setTrend) window.WC.setTrend(s.by_day.map(function (d) { return { label: d[0].slice(5).replace('-', '/'), value: d[1] }; }));
      $('.run-list').innerHTML = s.recent.length ? s.recent.map(function (r) {
        var ok = r.status === 'success';
        return '<li><span class="' + (ok ? 'ok' : 'fail') + '">' + (ok ? '●' : '●') + '</span><span class="run-title">' + U.esc(r.automation) +
          '</span><span class="run-time">' + U.timeAgo(r.at) + '</span></li>';
      }).join('') : '<li class="empty-li">아직 실행 기록이 없습니다</li>';
      retries = 0;
    }).catch(waiting);
  }

  function useSample(s) {
    var files = s.before.map(function (n) { return [n, 'before']; }).concat(s.after.map(function (n) { return [n, 'after']; }));
    Promise.all(files.map(function (f) {
      return fetch('/api/samples/excel/' + s.id + '/' + encodeURIComponent(f[0])).then(function (r) { return r.blob(); })
        .then(function (b) { return { file: new File([b], f[0]), slot: f[1] }; });
    })).then(function (list) {
      window.WC.fillQuick({
        prompt: s.prompt,
        before: list.filter(function (x) { return x.slot === 'before'; }).map(function (x) { return x.file; }),
        after: list.filter(function (x) { return x.slot === 'after'; }).map(function (x) { return x.file; })
      });
    });
  }

  // 로그인(또는 로컬 모드) 확인이 끝난 뒤에 불러오기
  window.WCAuth.ready.then(loadHome);
})();
