/* 업무 자동화 만들기 화면: AI가 레시피를 만들고 검증하는 과정 + 질문 답하기 + 저장(승인) */
(function () {
  'use strict';

  var U = window.WCU;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var icons = function (r) { window.WC.renderIcons(r); };

  var session = null, busy = false, proposal = null, saved = null, cards = {};
  var log = $('#b-log');

  /* ───── 탭 ───── */
  function showTab(name) {
    $$('[data-btab]').forEach(function (b) { b.classList.toggle('on', b.dataset.btab === name); });
    $$('[data-bpane]').forEach(function (p) { p.hidden = p.dataset.bpane !== name; });
  }
  $$('[data-btab]').forEach(function (b) { b.addEventListener('click', function () { showTab(b.dataset.btab); }); });
  function pane(n) { return $('[data-bpane="' + n + '"]'); }

  /* ───── 왼쪽 로그 ───── */
  function bubble(cls, html) {
    var d = document.createElement('div');
    d.className = 'msg ' + cls;
    d.innerHTML = html;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d;
  }

  function argText(o) {
    return Object.keys(o || {}).map(function (k) {
      var v = o[k];
      return k + '=' + (typeof v === 'string' ? '"' + v + '"' : JSON.stringify(v));
    }).join(', ');
  }

  function toolCall(ev) {
    if (ev.name === 'ask_user') return;
    var d = document.createElement('div');
    d.className = 'tool-card running';
    d.innerHTML = '<div class="tc-head"><span class="tc-ic"><i data-icon="loader" data-size="15"></i></span><strong>' + U.esc(ev.label) +
      '</strong><code>' + U.esc(ev.name) + '(' + U.esc(argText(ev.input)) + ')</code></div><div class="tc-sum">실행 중…</div>';
    log.appendChild(d);
    icons(d);
    cards[ev.id] = d;
    log.scrollTop = log.scrollHeight;
  }

  function toolResult(ev) {
    var d = cards[ev.id];
    if (!d) return;
    var good = ev.ok && !(ev.name === 'test_recipe' && ev.summary.indexOf('✓') < 0 && ev.summary.indexOf('일치율') >= 0);
    d.className = 'tool-card ' + (ev.ok ? (good ? 'ok' : 'warn') : 'fail');
    $('.tc-ic', d).innerHTML = '<i data-icon="' + (ev.ok ? (good ? 'check' : 'alert-triangle') : 'alert-triangle') + '" data-size="14"></i>';
    $('.tc-sum', d).textContent = ev.summary;
    icons(d);
  }

  function question(ev) {
    var d = bubble('question', '<div class="q-head"><i data-icon="help-circle" data-size="16"></i> AI가 확인을 요청합니다</div>' +
      '<p class="q-text">' + U.esc(ev.question) + '</p>' + (ev.why ? '<p class="q-why">' + U.esc(ev.why) + '</p>' : '') +
      '<div class="q-opts">' + (ev.options || []).map(function (o) { return '<button type="button">' + U.esc(o) + '</button>'; }).join('') + '</div>' +
      '<small class="q-free">또는 아래 입력칸에 직접 답을 적어 주세요.</small>');
    icons(d);
    $$('.q-opts button', d).forEach(function (b) {
      b.addEventListener('click', function () {
        $$('.q-opts button', d).forEach(function (x) { x.disabled = true; });
        b.classList.add('picked');
        send(b.textContent);
      });
    });
    $('#b-text').placeholder = '질문에 대한 답을 적어 주세요';
    $('#b-text').focus();
  }

  /* ───── 오른쪽 결과 ───── */
  function renderRecipe(ev) {
    var steps = ev.steps || [];
    $('#b-cnt-steps').textContent = steps.length;
    var inputs = (ev.recipe.inputs || []).map(function (i) {
      return '<li><b>' + U.esc(i.table) + '</b><small>필요한 열: ' + U.esc((i.columns || []).join(', ') || '-') + '</small></li>';
    }).join('');
    var params = (ev.recipe.params || []).map(function (p) {
      return '<li><b>' + U.esc(p.name) + '</b><small>' + U.esc(p.description || '') + ' (예시값 ' + U.esc(p.default) + ')</small></li>';
    }).join('');
    pane('steps').innerHTML =
      '<div class="recipe-io"><div><h4>입력</h4><ul>' + inputs + '</ul></div>' +
      (params ? '<div><h4>실행할 때 입력하는 값</h4><ul>' + params + '</ul></div>' : '') + '</div>' +
      '<ol class="step-list">' + steps.map(function (s) {
        var state = s.ok === false ? 'fail' : (s.ok ? 'ok' : '');
        return '<li class="' + state + '"><span class="st-no">' + s.step + '</span><div><b>' + U.esc(s.note || s.label) + '</b>' +
          '<p>' + U.esc(s.say) + '</p>' + (s.error ? '<p class="st-err">' + U.esc(s.error) + '</p>' : '') + '</div>' +
          '<span class="st-meta"><em>' + U.esc(s.label) + '</em>' + (s.rows != null ? s.rows.toLocaleString() + '행' : '') + '</span></li>';
      }).join('') + '</ol>' +
      '<p class="tbl-note">' + (ev.recipe.outputs || []).map(function (o) { return '결과: ' + U.esc(o.file || '') + ' / ' + U.esc(o.sheet || ''); }).join(' · ') + '</p>';

    var t = ev.test || {};
    if (!t.ok) {
      setVerdict(null, t.error ? '실행 오류: ' + t.error : '');
      return;
    }
    // 결과 미리보기
    var outs = t.outputs || {};
    pane('preview').innerHTML = Object.keys(outs).map(function (k) {
      var parts = k.split('|');
      return '<h4 class="pv-title">' + U.esc(parts[0]) + ' · ' + U.esc(parts[1]) + '</h4>' + U.previewTable(outs[k]);
    }).join('') || '<div class="empty">결과가 없습니다.</div>';
    // 검증
    var c = t.comparison;
    if (!c) {
      setVerdict(null, '업무 후 파일이 없어 자동 비교는 하지 않았습니다. 결과 미리보기를 직접 확인해 주세요.');
      pane('verify').innerHTML = '<div class="empty">업무 후 파일을 함께 올리면 셀 단위로 자동 검증합니다.</div>';
      return;
    }
    setVerdict(c, (c.perfect ? '업무 후 파일과 모든 칸·행·순서가 같습니다.' : (c.tables.map(function (x) { return (x.problems || []).join(' / '); }).join(' / ') || '차이가 있습니다')) +
      ((c.notes || []).length ? ' · ' + c.notes.join(' · ') : ''));
    pane('verify').innerHTML = c.tables.map(function (x) {
      var rows = (x.diffs || []).map(function (d) {
        return '<tr><td>' + U.esc(d.row) + '</td><td>' + U.esc(d.column) + '</td><td class="num">' + U.esc(U.fmt(d.expected)) +
          '</td><td class="num bad">' + U.esc(U.fmt(d.actual)) + '</td></tr>';
      }).join('');
      return '<div class="vf-table"><h4>' + U.esc(x.expected) + ' <span class="badge ' + (x.match_rate === 1 && !(x.problems || []).length ? 'ok' : 'warn') + '">' +
        (x.match_rate * 100).toFixed(1) + '%</span></h4>' +
        '<p class="tbl-note">' + (x.doc ? 'Word 문서 칸 비교' : '정답 ' + x.rows_expected + '행 · 결과 ' + x.rows_produced + '행') + ' · 맞은 칸 ' + U.fmt(x.cells_matched) + ' / ' + U.fmt(x.cells_total) +
        (x.row_key ? ' · 행 기준 열: ' + U.esc(x.row_key) : '') + '</p>' +
        ((x.problems || []).length ? '<ul class="problems">' + x.problems.map(function (p) { return '<li>' + U.esc(p) + '</li>'; }).join('') + '</ul>' : '<p class="all-ok">✓ 완전히 일치</p>') +
        (rows ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>행</th><th>열</th><th class="num">정답</th><th class="num">결과</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '') +
        '</div>';
    }).join('');
  }

  function setVerdict(c, text) {
    var el = $('#b-match');
    if (c) {
      el.textContent = (c.overall_match_rate * 100).toFixed(1) + '%';
      el.className = c.perfect ? 'good' : 'warn';
    } else {
      el.textContent = '-';
      el.className = '';
    }
    $('#b-verdict').textContent = text || '';
  }

  function renderSave() {
    var btn = $('#b-save'), name = $('#b-name'), hint = $('#b-save-hint');
    if (saved) {
      btn.disabled = true;
      name.disabled = true;
      btn.innerHTML = '저장됨';
      hint.innerHTML = '<a href="#" data-go="my">내 자동화에서 실행하기 →</a>';
      return;
    }
    if (proposal) {
      name.disabled = false;
      if (!name.value) name.value = proposal.name;
      btn.disabled = busy;
      hint.textContent = proposal.verified ? '검증 완료 · 단계를 확인하고 저장하세요' : '업무 후 파일 없이 만든 자동화입니다 · 결과를 확인하고 저장하세요';
      $('#b-title').textContent = proposal.name;
    } else {
      btn.disabled = true;
      name.disabled = true;
      hint.textContent = 'AI가 검증을 마치면 저장할 수 있습니다';
    }
  }

  $('#b-save').addEventListener('click', function () {
    if (!session || !proposal) return;
    var btn = this;
    btn.disabled = true;
    U.json('/api/sessions/' + session.id + '/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('#b-name').value, summary: proposal.summary })
    }).then(function (item) {
      saved = item;
      bubble('system', '✓ "' + U.esc(item.name) + '" 자동화를 저장했습니다. 다음부터는 새 파일만 올려 실행하세요.');
      renderSave();
      icons($('.approval-bar', $('#page-build')));
    }).catch(function (e) { bubble('error', U.esc(e.message)); btn.disabled = false; });
  });

  /* ───── 통신 ───── */
  function setBusy(v) {
    busy = v;
    $('#b-status').innerHTML = v ? '<i data-icon="loader" data-size="14"></i> AI가 작업 중' : '';
    icons($('#b-status'));
    $('#b-text').disabled = v;
    $('button[type=submit]', $('#b-form')).disabled = v;
    $('#b-clip').disabled = v;
    renderSave();
  }

  function handle(ev) {
    if (ev.type === 'mode') {
      var b = $('#b-mode');
      b.className = 'mode-badge ' + ev.mode;
      b.textContent = ev.mode === 'openai' ? 'OpenAI · ' + ev.model : ev.mode === 'claude' ? 'Claude · ' + ev.model : 'AI 키 없음';
    } else if (ev.type === 'usage') { if (window.WCAuth) window.WCAuth.setUsage(ev); }
    else if (ev.type === 'assistant') bubble('ai', U.md(ev.text));
    else if (ev.type === 'tool_call') toolCall(ev);
    else if (ev.type === 'tool_result') toolResult(ev);
    else if (ev.type === 'recipe') { renderRecipe(ev); showTab(ev.test && ev.test.comparison && !ev.test.comparison.perfect ? 'verify' : 'steps'); }
    else if (ev.type === 'question') question(ev);
    else if (ev.type === 'proposal') { proposal = ev; bubble('system', '저장 승인 요청: <b>' + U.esc(ev.name) + '</b> — ' + U.esc(ev.summary)); }
    else if (ev.type === 'build_state') { if (ev.proposal) proposal = ev.proposal; }
    else if (ev.type === 'error') bubble('error', U.esc(ev.message));
  }

  function send(text) {
    if (!session || busy || !text.trim()) return;
    bubble('user', U.md(text));
    $('#b-text').placeholder = '추가 설명이나 수정 요청 (Shift+Enter 줄바꿈)';
    setBusy(true);
    var wait = bubble('ai pending', '<span class="dots"><i></i><i></i><i></i></span>');
    U.stream('/api/sessions/' + session.id + '/messages', { text: text }, function (ev) {
      handle(ev);
      log.appendChild(wait);
      log.scrollTop = log.scrollHeight;
    }).catch(function (e) { bubble('error', U.esc(e.message)); })
      .then(function () { wait.remove(); setBusy(false); });
  }

  /* ───── 대화 중 파일 첨부 ───── */
  var pending = [];   // [{file, role: '업무 전'|'업무 후'|'참고 문서'}]
  function isDoc(name) { return /\.(md|txt)$/i.test(name); }
  function renderPending() {
    var box = $('#b-attach');
    box.hidden = !pending.length;
    box.innerHTML = pending.map(function (p, i) {
      var cls = p.role === '업무 후' ? 'after' : p.role === '참고 문서' ? 'doc' : '';
      return '<span class="ca-chip"><b class="ca-role ' + cls + '" data-i="' + i + '" title="눌러서 업무 전 ↔ 업무 후 바꾸기">' + p.role + '</b>' +
        '<span>' + U.esc(p.file.name) + '</span><button type="button" class="ca-x" data-x="' + i + '" aria-label="빼기">×</button></span>';
    }).join('') + (pending.length ? '<span class="ca-hint">보내기를 누르면 파일이 추가되고 AI가 다시 확인합니다. 칸 이름을 누르면 업무 전/업무 후를 바꿀 수 있어요.</span>' : '');
  }
  $('#b-clip').addEventListener('click', function () { $('#b-file').click(); });
  $('#b-file').addEventListener('change', function () {
    Array.prototype.forEach.call(this.files, function (f) {
      pending = pending.filter(function (p) { return p.file.name !== f.name; });
      pending.push({ file: f, role: isDoc(f.name) ? '참고 문서' : '업무 전' });
    });
    this.value = '';
    renderPending();
    $('#b-text').focus();
  });
  $('#b-attach').addEventListener('click', function (e) {
    var r = e.target.closest('[data-i]'), x = e.target.closest('[data-x]');
    if (r && pending[+r.dataset.i].role !== '참고 문서') {
      var p = pending[+r.dataset.i];
      p.role = p.role === '업무 전' ? '업무 후' : '업무 전';
    } else if (x) pending.splice(+x.dataset.x, 1);
    renderPending();
  });

  function uploadPending() {
    var fd = new FormData();
    pending.forEach(function (p) { fd.append(p.role === '업무 후' ? 'after' : 'before', p.file, p.file.name); });
    return fetch('/api/sessions/' + session.id + '/files', { method: 'POST', body: fd }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.detail || ('파일을 추가하지 못했습니다 (' + r.status + ')'));
        return j;
      });
    });
  }

  function addHeaderChips(files) {
    $('#b-files').insertAdjacentHTML('beforeend', files.map(function (f) {
      return '<span class="file-chip ' + (f.label === '업무 후' ? 'after' : '') + '"><i data-icon="paperclip" data-size="12"></i>' +
        U.esc(f.label) + ' · ' + U.esc(f.name) + '</span>';
    }).join(''));
    icons($('#b-files'));
  }

  $('#b-form').addEventListener('submit', function (e) {
    e.preventDefault();
    if (!session || busy) return;
    var t = $('#b-text').value;
    if (!pending.length) { $('#b-text').value = ''; $('#b-text').dispatchEvent(new Event('input')); send(t); return; }
    setBusy(true);
    uploadPending().then(function (r) {
      pending = []; renderPending();
      $('#b-text').value = ''; $('#b-text').dispatchEvent(new Event('input'));
      if (r.added.length) {
        addHeaderChips(r.added);
        bubble('system', '파일 추가: ' + r.added.map(function (f) { return '<b>' + U.esc(f.label) + '</b> ' + U.esc(f.name); }).join(', '));
      }
      (r.errors || []).forEach(function (m) { bubble('error', U.esc(m)); });
      setBusy(false);
      if (r.added.length) send(t.trim() ? t : '파일을 추가했어요. 새 파일을 확인하고 이어서 진행해 주세요.');
    }).catch(function (err) { setBusy(false); bubble('error', U.esc(err.message)); });
  });
  U.chatInput($('#b-text'), $('#b-form'));

  function open(info, prompt) {
    session = { id: info.session_id, files: info.files };
    proposal = null; saved = null; cards = {};
    pending = []; renderPending();
    log.innerHTML = '';
    $('#b-title').textContent = '새 자동화';
    $('#b-name').value = '';
    $('#b-cnt-steps').textContent = '';
    setVerdict(null, 'AI가 레시피를 만들고 업무 후 파일과 셀 단위로 비교합니다.');
    pane('steps').innerHTML = '<div class="empty">AI가 만든 처리 단계가 여기에 표시됩니다.</div>';
    pane('verify').innerHTML = '<div class="empty">업무 후 파일과 비교한 결과가 여기에 표시됩니다.</div>';
    pane('preview').innerHTML = '<div class="empty">레시피로 만든 결과 표가 여기에 표시됩니다.</div>';
    showTab('steps');
    $('#b-files').innerHTML = info.files.map(function (f) {
      return '<span class="file-chip ' + (f.label === '업무 후' ? 'after' : '') + '"><i data-icon="paperclip" data-size="12"></i>' +
        U.esc(f.label) + ' · ' + U.esc(f.name) + '</span>';
    }).join('');
    icons($('#b-files'));
    renderSave();
    setTimeout(function () { send(prompt); }, 50);
  }

  window.WCBuild = { open: open };
})();
