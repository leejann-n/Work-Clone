/* 에이전트 작업 화면 — 서버(FastAPI)와 통신하며 AI의 도구 호출 과정과 결과를 실시간으로 보여 줍니다. */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var icons = function (root) { window.WC.renderIcons(root); };

  var session = null;      // { id, mode, files }
  var busy = false;
  var state = { calc: null, checks: null, review: null, files: [], approvals: { 담당자: null, 부장: null, 대표: null } };

  var log = $('#chat-log');
  var form = $('#chat-form');
  var input = $('#chat-text');

  /* ───── 유틸 ───── */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function won(n) { return (n || 0).toLocaleString('ko-KR'); }
  function scrollLog() { log.scrollTop = log.scrollHeight; }

  // 아주 작은 Markdown 변환기 (제목, 굵게, 표, 목록, 문단)
  function md(text) {
    var lines = esc(text).split('\n');
    var html = '', i = 0;
    function inline(s) {
      return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/(^|[^*])_([^_]+)_(?!\w)/g, '$1<em>$2</em>');
    }
    while (i < lines.length) {
      var l = lines[i];
      if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        var head = l.trim().slice(1, -1).split('|');
        html += '<div class="md-table"><table><thead><tr>' + head.map(function (h) { return '<th>' + inline(h.trim()) + '</th>'; }).join('') + '</tr></thead><tbody>';
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          html += '<tr>' + lines[i].trim().slice(1, -1).split('|').map(function (c) { return '<td>' + inline(c.trim()) + '</td>'; }).join('') + '</tr>';
          i++;
        }
        html += '</tbody></table></div>';
        continue;
      }
      var h = l.match(/^(#{1,4})\s+(.*)$/);
      if (h) { html += '<h' + (h[1].length + 2) + '>' + inline(h[2]) + '</h' + (h[1].length + 2) + '>'; i++; continue; }
      if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
        var ordered = /^\s*\d+\./.test(l);
        html += ordered ? '<ol>' : '<ul>';
        while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          html += '<li>' + inline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, '')) + '</li>';
          i++;
        }
        html += ordered ? '</ol>' : '</ul>';
        continue;
      }
      if (!l.trim()) { i++; continue; }
      var para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i])) { para.push(inline(lines[i])); i++; }
      if (para.length) html += '<p>' + para.join('<br>') + '</p>'; else i++;
    }
    return html;
  }

  /* ───── 왼쪽: 대화 · 도구 호출 로그 ───── */
  function addBubble(role, html) {
    var div = document.createElement('div');
    div.className = 'msg ' + role;
    div.innerHTML = html;
    log.appendChild(div);
    scrollLog();
    return div;
  }

  function argText(input) {
    var keys = Object.keys(input || {});
    if (!keys.length) return '';
    return keys.map(function (k) {
      var v = input[k];
      if (Array.isArray(v)) return k + '=[' + v.length + '개]';
      return k + '=' + (typeof v === 'string' ? '"' + v + '"' : JSON.stringify(v));
    }).join(', ');
  }

  var toolCards = {};
  function toolCall(ev) {
    var div = document.createElement('div');
    div.className = 'tool-card running';
    div.innerHTML =
      '<div class="tc-head"><span class="tc-ic"><i data-icon="loader" data-size="15"></i></span>' +
      '<strong>' + esc(ev.label) + '</strong><code>' + esc(ev.name) + '(' + esc(argText(ev.input)) + ')</code></div>' +
      '<div class="tc-sum">실행 중…</div>';
    log.appendChild(div);
    icons(div);
    toolCards[ev.id] = div;
    scrollLog();
  }

  function toolResult(ev) {
    var div = toolCards[ev.id];
    if (!div) return;
    div.className = 'tool-card ' + (ev.ok ? 'ok' : 'fail');
    var ic = $('.tc-ic', div);
    ic.innerHTML = '<i data-icon="' + (ev.ok ? 'check' : 'alert-triangle') + '" data-size="14"></i>';
    $('.tc-sum', div).innerHTML = esc(ev.summary) +
      (ev.file_url ? ' <a class="tc-link" href="' + esc(ev.file_url) + '" download><i data-icon="download" data-size="13"></i> 받기</a>' : '');
    if (ev.name === 'search_docs' && ev.ok && ev.data.results.length) {
      var d = document.createElement('details');
      d.innerHTML = '<summary>찾은 근거 ' + ev.data.results.length + '건</summary>' +
        ev.data.results.map(function (h) {
          return '<div class="hit"><b>' + esc(h.document) + '</b> · ' + esc(h.section) + (h.date ? ' · ' + esc(h.date) : '') +
            '<p>' + esc(h.text.slice(0, 220)) + (h.text.length > 220 ? '…' : '') + '</p></div>';
        }).join('');
      div.appendChild(d);
    }
    icons(div);
    applyResult(ev);
    scrollLog();
  }

  /* ───── 오른쪽: 결과 패널 ───── */
  function showTab(name) {
    $$('.tabs [data-tab]').forEach(function (b) { b.classList.toggle('on', b.dataset.tab === name); });
    $$('.tab-pane').forEach(function (p) { p.hidden = p.dataset.pane !== name; });
  }
  $$('.tabs [data-tab]').forEach(function (b) { b.addEventListener('click', function () { showTab(b.dataset.tab); }); });

  function pane(name) { return $('.tab-pane[data-pane="' + name + '"]'); }

  function applyResult(ev) {
    if (!ev.ok) return;
    var d = ev.data;
    if (ev.name === 'calc_payroll') { state.calc = d; renderCalc(); showTab('calc'); }
    if (ev.name === 'check_anomalies') { state.checks = d; renderChecks(); showTab('check'); }
    if (ev.name === 'save_review_table') { state.review = d.rows; renderReview(); showTab('review'); }
    if (ev.file_url) {
      state.files = state.files.filter(function (f) { return f.name !== d.file; });
      state.files.push({ name: d.file, url: ev.file_url, kind: ev.name === 'make_transfer_file' ? '이체 파일' : '검토용 엑셀', extra: d });
      renderFiles();
      if (ev.name === 'make_transfer_file') showTab('files');
    }
  }

  function renderCalc() {
    var c = state.calc, t = c.totals;
    $('#kpi-count').textContent = t.인원 + '명';
    $('#kpi-gross').textContent = won(t.지급총액) + '원';
    $('#kpi-ded').textContent = won(t.공제합계) + '원';
    $('#kpi-net').textContent = won(t.실수령액) + '원';
    $('#cnt-calc').textContent = t.인원;
    var cols = ['기본급', '직책수당', '식대', '시간외수당', '성과급', '지급총액', '공제합계', '실수령액'];
    var rows = c.employees.map(function (e) {
      var special = e.적용규칙.filter(function (n) { return n.indexOf('지급월 아님') < 0; });
      return '<tr' + (special.length ? ' class="flag"' : '') + '>' +
        '<td>' + esc(e.사번) + '</td><td><b>' + esc(e.이름) + '</b> <small>' + esc(e.직책) + '</small>' +
        special.map(function (n) { return '<span class="rule">' + esc(n) + '</span>'; }).join('') + '</td>' +
        '<td class="num">' + e.근무일수 + '/' + e.역일수 + '</td>' +
        cols.map(function (k) { return '<td class="num' + (k === '실수령액' ? ' strong' : '') + '">' + won(e[k]) + '</td>'; }).join('') + '</tr>';
    }).join('');
    pane('calc').innerHTML =
      '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>사번</th><th>이름 · 적용 규칙</th><th>근무일</th>' +
      cols.map(function (k) { return '<th class="num">' + k + '</th>'; }).join('') + '</tr></thead><tbody>' + rows +
      '</tbody><tfoot><tr><td colspan="3">합계 ' + t.인원 + '명</td>' +
      cols.map(function (k) { return '<td class="num">' + won(t[k] != null ? t[k] : sum(c.employees, k)) + '</td>'; }).join('') +
      '</tr></tfoot></table></div>' +
      '<p class="tbl-note">' + (c.bonus_month ? '성과급 지급월입니다.' : c.pay_month.slice(5) * 1 + '월은 성과급 지급월이 아닙니다 (급여규정 제12조).') +
      ' 모든 금액은 도구(코드)가 계산했습니다.</p>';
  }
  function sum(list, k) { return list.reduce(function (a, e) { return a + (e[k] || 0); }, 0); }

  var STATUS = { ok: ['✔', '정상', 'ok'], warn: ['⚠', '확인 필요', 'warn'], error: ['✖', '오류', 'error'] };
  function renderChecks() {
    var s = state.checks.summary;
    $('#kpi-check').innerHTML = '<span class="c-ok">✔' + s.ok + '</span> <span class="c-warn">⚠' + s.warn + '</span>' + (s.error ? ' <span class="c-error">✖' + s.error + '</span>' : '');
    $('#cnt-check').textContent = s.warn + s.error || '';
    pane('check').innerHTML = '<ul class="check-list">' + state.checks.checks.map(function (c) {
      var st = STATUS[c.결과];
      return '<li class="' + st[2] + '"><span class="badge ' + st[2] + '">' + st[0] + ' ' + st[1] + '</span>' +
        '<div><b>' + esc(c.항목) + '</b><p>' + esc(c.내용) + '</p></div></li>';
    }).join('') + '</ul>';
  }

  var RSTATUS = { '근거 확인': 'ok', '확인 요청': 'warn', '근거 부족': 'error' };
  function renderReview() {
    $('#cnt-review').textContent = state.review.length;
    pane('review').innerHTML = '<div class="tbl-wrap"><table class="tbl review"><thead><tr><th>항목</th><th>처리 내용</th><th>근거 문서</th><th>상태</th></tr></thead><tbody>' +
      state.review.map(function (r) {
        return '<tr><td><b>' + esc(r.항목) + '</b></td><td>' + esc(r['처리 내용']) + '</td><td>' + esc(r['근거 문서']) +
          '</td><td><span class="badge ' + (RSTATUS[r.상태] || 'warn') + '">' + esc(r.상태) + '</span></td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  function renderFiles() {
    $('#cnt-files').textContent = state.files.length || '';
    var uploaded = (session && session.files || []).map(function (f) {
      return '<li><span class="f-ic up"><i data-icon="paperclip" data-size="16"></i></span><div><b>' + esc(f.name) + '</b><small>올린 파일 · ' + esc(f.label) + '</small></div></li>';
    }).join('');
    var made = state.files.map(function (f) {
      var extra = f.kind === '이체 파일'
        ? ' · ' + f.extra.count + '명 · ' + won(f.extra.transfer_total) + '원 · 계산값과 ' + (f.extra.total_match ? '일치' : '불일치')
        : ' · 시트: ' + (f.extra.sheets || []).join(', ');
      return '<li><span class="f-ic"><i data-icon="file-spreadsheet" data-size="16"></i></span><div><b>' + esc(f.name) + '</b><small>' + f.kind + esc(extra) + '</small></div>' +
        '<a class="btn-outline" href="' + esc(f.url) + '" download><i data-icon="download" data-size="14"></i> 받기</a></li>';
    }).join('');
    pane('files').innerHTML = '<ul class="file-rows">' + made + uploaded + '</ul>';
    icons(pane('files'));
  }

  /* ───── 승인 (급여규정 제14조) ───── */
  var ORDER = ['담당자', '부장', '대표'];
  function renderApproval(ready) {
    var a = state.approvals;
    var all = ORDER.every(function (r) { return a[r]; });
    $$('.ap-step').forEach(function (b, i) {
      var role = b.dataset.role, done = !!a[role];
      var prevDone = ORDER.slice(0, i).every(function (r) { return a[r]; });
      b.classList.toggle('done', done);
      b.disabled = busy || !ready || done || !prevDone;
      $('span', b).textContent = done ? '✓' : String(i + 1);
    });
    $('#ap-transfer').disabled = busy || !all || state.files.some(function (f) { return f.kind === '이체 파일'; });
  }

  $$('.ap-step').forEach(function (b) {
    b.addEventListener('click', function () {
      if (!session) return;
      b.disabled = true;
      fetch('/api/sessions/' + session.id + '/approve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: b.dataset.role, name: b.dataset.role })
      }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail); return j; }); })
        .then(function (j) {
          state.approvals = j.approvals;
          addBubble('system', '✓ ' + esc(b.dataset.role) + ' 승인 완료' + (j.fully_approved ? ' — 3단계 승인이 끝났습니다. 이체 파일을 만들 수 있습니다.' : ''));
          renderApproval(true);
        })
        .catch(function (e) { addBubble('error', esc(e.message)); renderApproval(true); });
    });
  });
  $('#ap-transfer').addEventListener('click', function () {
    send('3단계 승인(담당자·부장·대표)을 모두 마쳤습니다. 승인합니다. 이체 파일을 만들어 주세요.');
  });

  /* ───── 서버 통신 ───── */
  function setBusy(v, label) {
    busy = v;
    $('#run-status').innerHTML = v ? '<i data-icon="loader" data-size="14"></i> ' + (label || 'AI가 작업 중') : '';
    icons($('#run-status'));
    $('button', form).disabled = v;
    input.disabled = v;
    renderApproval(!!state.calc);
  }

  function send(text) {
    if (!session || busy || !text.trim()) return;
    addBubble('user', md(text));
    setBusy(true);
    var thinking = addBubble('ai pending', '<span class="dots"><i></i><i></i><i></i></span>');
    fetch('/api/sessions/' + session.id + '/messages', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text })
    }).then(function (res) {
      if (!res.ok || !res.body) {
        return res.json().catch(function () { return {}; }).then(function (j) { throw new Error(j.detail || ('서버 응답 오류 (' + res.status + ')')); });
      }
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = '';
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          var lines = buf.split('\n');
          buf = lines.pop();
          lines.forEach(function (line) {
            if (!line.trim()) return;
            var ev;
            try { ev = JSON.parse(line); } catch (e) { return; }
            handle(ev, thinking);
            log.appendChild(thinking);   // '작업 중' 표시를 항상 맨 아래에
          });
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      addBubble('error', esc(e.message));
    }).then(function () {
      thinking.remove();
      setBusy(false);
      scrollLog();
    });
  }

  function handle(ev) {
    if (ev.type === 'mode') {
      var badge = $('#run-mode');
      badge.className = 'mode-badge ' + ev.mode;
      var label = { claude: 'Claude · ', openai: 'OpenAI · ' }[ev.mode];
      badge.textContent = label ? label + ev.model : '데모 모드 (API 키 없음)';
      badge.title = label ? 'AI가 도구를 스스로 선택합니다 (tool calling)'
        : '정해진 순서로 같은 도구를 실행합니다. OPENAI_API_KEY 또는 ANTHROPIC_API_KEY를 설정하면 AI가 직접 판단합니다.';
    } else if (ev.type === 'usage') { if (window.WCAuth) window.WCAuth.setUsage(ev); }
    else if (ev.type === 'assistant') addBubble('ai', md(ev.text));
    else if (ev.type === 'tool_call') toolCall(ev);
    else if (ev.type === 'tool_result') toolResult(ev);
    else if (ev.type === 'approval') { state.approvals = ev.approvals; renderApproval(ev.ready); }
    else if (ev.type === 'error') addBubble('error', esc(ev.message));
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var t = input.value;
    input.value = '';
    input.dispatchEvent(new Event('input'));   // 입력칸 높이 되돌리기
    send(t);
  });
  window.WCU.chatInput(input, form);

  $('#run-back').addEventListener('click', function () { window.WC.navigate('home'); });

  /* ───── 시작 (홈의 'AI 분석 시작'에서 호출) ───── */
  function reset() {
    log.innerHTML = '';
    toolCards = {};
    state = { calc: null, checks: null, review: null, files: [], approvals: { 담당자: null, 부장: null, 대표: null } };
    ['kpi-count', 'kpi-gross', 'kpi-ded', 'kpi-net', 'kpi-check'].forEach(function (id) { $('#' + id).textContent = '-'; });
    ['cnt-calc', 'cnt-check', 'cnt-review', 'cnt-files'].forEach(function (id) { $('#' + id).textContent = ''; });
    pane('calc').innerHTML = '<div class="empty">AI가 급여를 계산하면 여기에 표시됩니다.</div>';
    pane('check').innerHTML = '<div class="empty">이체 전 점검 결과가 여기에 표시됩니다.</div>';
    pane('review').innerHTML = '<div class="empty">규칙이 적용된 항목과 근거 문서가 여기에 표시됩니다.</div>';
    showTab('calc');
  }

  function start(opts) {
    if (location.protocol === 'file:') {
      return Promise.reject(new Error('서버를 실행한 뒤 http://localhost:8000 으로 접속해 주세요 (README 참고).'));
    }
    var fd = new FormData();
    fd.append('prompt', opts.prompt);
    opts.before.forEach(function (f) { fd.append('before', f, f.name); });
    opts.after.forEach(function (f) { fd.append('after', f, f.name); });
    return fetch('/api/sessions', { method: 'POST', body: fd })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || '세션을 만들 수 없습니다.'); return j; }); },
            function () { throw new Error('서버에 연결할 수 없습니다. backend 서버가 실행 중인지 확인하세요.'); })
      .then(function (j) {
        // 서버가 업무 종류를 판단: build(업무 예시로 자동화 만들기) / payroll(규정 기반 급여 정산)
        if (j.kind === 'build') {
          window.WCBuild.open(j, opts.prompt);
          return 'build';
        }
        reset();
        session = { id: j.session_id, mode: j.mode, files: j.files };
        $('#run-files').innerHTML = j.files.map(function (f) {
          return '<span class="file-chip"><i data-icon="paperclip" data-size="12"></i>' + esc(f.name) + '</span>';
        }).join('');
        icons($('#run-files'));
        renderFiles();
        setTimeout(function () { send(opts.prompt); }, 50);
        return 'run';
      });
  }

  window.WCRun = { start: start, hasSession: function () { return !!session; } };
})();
