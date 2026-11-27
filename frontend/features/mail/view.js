/* 업무 메일 (담당: PM) */
(function () {
  var U = window.WCU, api = WCFeature.api;
  var box = 'inbox', current = null;
  function el(root, n) { return root.querySelector('[data-el="' + n + '"]'); }
  function when(s) {
    var d = new Date(s);
    if (isNaN(d)) return '';
    var today = new Date().toDateString() === d.toDateString();
    return today ? d.toTimeString().slice(0, 5) : (d.getMonth() + 1) + '/' + d.getDate();
  }
  function icons(root) { if (window.WC && WC.renderIcons) WC.renderIcons(root); }
  function setBadge(n) {
    var b = document.querySelector('.nav-item[data-page="mail"] .nav-badge');
    if (b) { b.textContent = n; b.style.display = n ? '' : 'none'; }
  }

  function loadList(root, keep) {
    api('/api/mail/list?box=' + box).then(function (rows) {
      var unread = rows.filter(function (m) { return !m.is_read; }).length;
      el(root, 'count').textContent = box === 'inbox' ? '안 읽은 메일 ' + unread + '통' : '보낸 메일 ' + rows.length + '통';
      if (box === 'inbox') setBadge(unread);
      el(root, 'list').innerHTML = rows.map(function (m) {
        var who = box === 'sent' ? '받는 사람: ' + (m.to_name || '') : m.from_name;
        return '<li data-id="' + U.esc(m.id) + '" class="' + (m.is_read || box === 'sent' ? '' : 'unread') + (keep && keep === m.id ? ' on' : '') + '">' +
          '<div class="ml-from"><b>' + U.esc(who) + '</b><span>' + when(m.received_at) + '</span></div>' +
          '<div class="ml-subj">' + ((m.attachments || []).length ? '📎 ' : '') + U.esc(m.subject) +
          (m.category ? '<span class="ml-tag">' + U.esc(m.category) + '</span>' : '') +
          (m.run_id ? '<span class="ml-tag run">자동화 실행됨</span>' : '') + '</div></li>';
      }).join('') || '<li class="empty">' + (box === 'inbox' ? '받은 메일이 없습니다.' : '보낸 메일이 없습니다.') + '</li>';
    }).catch(function (e) { el(root, 'list').innerHTML = '<li class="fp-error">' + U.esc(e.message) + '</li>'; });
  }

  function fileLinks(m) {
    return (m.attachments || []).map(function (a, i) {
      return a.has_file ? '<a download="' + U.esc(a.name) + '" href="/api/mail/' + encodeURIComponent(m.id) + '/files/' + i + '"><i data-icon="paperclip" data-size="13"></i>' + U.esc(a.name) + '</a>'
                        : '<a aria-disabled="true"><i data-icon="paperclip" data-size="13"></i>' + U.esc(a.name) + ' (파일 없음)</a>';
    }).join('');
  }

  function showMail(root, m) {
    current = m;
    var main = el(root, 'main');
    var hasTables = (m.attachments || []).some(function (a) { return a.has_file && /\.(xlsx|xlsm|csv|pdf)$/i.test(a.name); });
    main.innerHTML =
      '<div class="ml-head"><h3>' + U.esc(m.subject) + '</h3><div class="ml-meta">' +
      (box === 'sent' ? '받는 사람 ' + U.esc(m.to_name || '') : U.esc(m.from_name) + ' &lt;' + U.esc(m.from_email) + '&gt;') + ' · ' +
      U.esc(String(m.received_at).slice(0, 16).replace('T', ' ')) + (m.category ? ' <span class="ml-tag">' + U.esc(m.category) + '</span>' : '') + '</div></div>' +
      ((m.attachments || []).length ? '<div class="ml-files">' + fileLinks(m) + '</div>' : '') +
      '<div class="ml-body">' + U.esc(m.body || '') + '</div>' +
      (hasTables ? '<div class="ml-box"><h4><i data-icon="zap" data-size="15"></i> 첨부파일로 자동화 실행</h4>' +
        '<p>첨부파일에 바로 쓸 수 있는 저장된 자동화를 찾습니다 (AI 사용 안 함).</p>' +
        '<div data-el="auto"><button type="button" class="btn-primary sm" data-act="suggest">맞는 자동화 찾기</button></div></div>' : '') +
      (box === 'inbox' ? '<div class="ml-box"><h4><i data-icon="sparkles" data-size="15"></i> AI 도우미</h4>' +
        '<div data-el="assist">' + (m.summary ? assistView(m) : '<p>분류 · 한 줄 요약 · 답장 초안을 만들어 드립니다 (AI 1회 사용).</p>' +
        '<button type="button" class="btn-primary sm" data-act="assist">요약하고 답장 초안 쓰기</button>') + '</div></div>' : '');
    icons(main);
  }

  function assistView(r) {
    return '<p><b>분류</b> ' + U.esc(r.category || '') + '</p><p><b>요약</b> ' + U.esc(r.summary || '') + '</p>' +
      '<textarea data-el="reply" style="min-height:140px">' + U.esc(r.reply_draft || '') + '</textarea>' +
      '<div class="fp-row" style="margin-top:8px"><button type="button" class="btn-primary sm" data-act="reply">답장 보내기</button>' +
      '<span data-el="reply-msg" class="ml-ok"></span></div>';
  }

  function runResult(r) {
    return '<div class="ml-result"><p class="ml-ok">✓ ' + U.esc(r.automation) + ' 실행 완료</p>' +
      r.files.map(function (f) { return '<a download="' + U.esc(f.name) + '" href="' + U.esc(f.url) + '" class="btn-primary sm" style="display:inline-flex;gap:6px;align-items:center;text-decoration:none"><i data-icon="download" data-size="13"></i>' + U.esc(f.name) + '</a>'; }).join('') +
      '<p>' + (r.checks || []).map(function (c) { return U.esc(c['항목'] + ': ' + c['내용']); }).join('<br />') + '</p>' +
      '<p><a href="#" data-go="history">실행 이력에서 보기 →</a></p></div>';
  }

  function compose(root, preset) {
    current = null;
    var main = el(root, 'main');
    main.innerHTML = '<div class="ml-head"><h3>새 메일</h3></div><form class="ml-form" data-el="form">' +
      '<select name="to" required><option value="">받는 사람 선택…</option></select>' +
      '<input type="text" name="subject" placeholder="제목" required value="' + U.esc((preset && preset.subject) || '') + '" />' +
      '<textarea name="body" placeholder="내용">' + U.esc((preset && preset.body) || '') + '</textarea>' +
      '<input type="file" name="files" multiple />' +
      '<div class="fp-row"><button type="submit" class="btn-primary sm"><i data-icon="send" data-size="14"></i> 보내기</button><span class="fp-error" data-el="send-err"></span></div></form>';
    icons(main);
    api('/api/mail/people').then(function (ps) {
      main.querySelector('select').innerHTML += ps.map(function (p) {
        return '<option value="' + U.esc(p.emp_no) + '">' + U.esc(p.name + ' ' + (p.position || '') + ' · ' + (p.department || '') + ' (' + p.emp_no + ')') + '</option>';
      }).join('');
    });
    el(root, 'form').addEventListener('submit', function (e) {
      e.preventDefault();
      var fd = new FormData(this), btn = this.querySelector('button[type=submit]');
      btn.disabled = true;
      api('/api/mail/send', { body: fd }).then(function () {
        box = 'sent';
        root.querySelectorAll('[data-box]').forEach(function (b) { b.classList.toggle('on', b.dataset.box === 'sent'); });
        main.innerHTML = '<div class="empty ml-empty">✓ 메일을 보냈습니다.</div>';
        loadList(root);
      }).catch(function (err) { el(root, 'send-err').textContent = err.message; btn.disabled = false; });
    });
  }

  WCFeature.register('mail', {
    init: function (root) {
      root.querySelectorAll('[data-box]').forEach(function (b) {
        b.addEventListener('click', function () {
          box = b.dataset.box;
          root.querySelectorAll('[data-box]').forEach(function (x) { x.classList.toggle('on', x === b); });
          el(root, 'main').innerHTML = '<div class="empty ml-empty">왼쪽에서 메일을 고르세요.</div>';
          loadList(root);
        });
      });
      el(root, 'list').addEventListener('click', function (e) {
        var li = e.target.closest('li[data-id]');
        if (!li) return;
        root.querySelectorAll('.ml-list li.on').forEach(function (x) { x.classList.remove('on'); });
        li.classList.add('on');
        api('/api/mail/' + encodeURIComponent(li.dataset.id)).then(function (m) {
          if (li.classList.contains('unread')) { li.classList.remove('unread'); loadList(root, m.id); }
          showMail(root, m);
        }).catch(function (err) { el(root, 'main').innerHTML = '<p class="fp-error">' + U.esc(err.message) + '</p>'; });
      });
      el(root, 'compose-btn').addEventListener('click', function () { compose(root); });
      // 메일 본문 안의 버튼들
      el(root, 'main').addEventListener('click', function (e) {
        var b = e.target.closest('[data-act]');
        if (!b || !current) return;
        var m = current, act = b.dataset.act;
        if (act === 'suggest') {
          var auto = el(root, 'auto');
          auto.innerHTML = '<p>찾는 중…</p>';
          api('/api/mail/' + encodeURIComponent(m.id) + '/suggest').then(function (r) {
            var html = (r.candidates || []).map(function (c) {
              return '<div class="ml-cand"><b>' + U.esc(c.name) + '</b><small>' + U.esc(c.owner_name || '') + '</small>' +
                '<button type="button" class="btn-primary sm" data-act="run" data-aid="' + U.esc(c.id) + '">실행</button></div>';
            }).join('');
            if (!html) html = '<p>' + U.esc(r.note || '이 첨부파일에 바로 쓸 수 있는 자동화가 없습니다.') + '</p>' +
              ((r.others || []).length ? '<p><small>열 구성이 맞지 않는 자동화: ' + r.others.map(function (o) { return U.esc(o.name); }).join(', ') + '</small></p>' : '') +
              '<p><a href="#" data-go="create">이 첨부파일로 새 자동화 만들기 →</a></p>';
            auto.innerHTML = html;
          }).catch(function (err) { auto.innerHTML = '<p class="fp-error">' + U.esc(err.message) + '</p>'; });
        } else if (act === 'run') {
          b.disabled = true; b.textContent = '실행 중…';
          api('/api/mail/' + encodeURIComponent(m.id) + '/run', { body: { automation_id: b.dataset.aid } }).then(function (r) {
            el(root, 'auto').insertAdjacentHTML('beforeend', runResult(r));
            icons(el(root, 'auto'));
            b.textContent = '완료';
            loadList(root, m.id);
          }).catch(function (err) { b.disabled = false; b.textContent = '다시 실행'; el(root, 'auto').insertAdjacentHTML('beforeend', '<p class="fp-error">' + U.esc(err.message) + '</p>'); });
        } else if (act === 'assist') {
          b.disabled = true; b.textContent = 'AI 작성 중…';
          api('/api/mail/' + encodeURIComponent(m.id) + '/assist', { method: 'POST' }).then(function (r) {
            el(root, 'assist').innerHTML = assistView(r);
            m.category = r.category; m.summary = r.summary;
            loadList(root, m.id);
          }).catch(function (err) { b.disabled = false; b.textContent = '다시 시도'; el(root, 'assist').insertAdjacentHTML('beforeend', '<p class="fp-error">' + U.esc(err.message) + '</p>'); });
        } else if (act === 'reply') {
          b.disabled = true;
          api('/api/mail/' + encodeURIComponent(m.id) + '/reply', { body: { body: el(root, 'reply').value } }).then(function (r) {
            el(root, 'reply-msg').textContent = '✓ ' + r.note;
          }).catch(function (err) { b.disabled = false; el(root, 'reply-msg').textContent = err.message; });
        }
      });
    },
    show: function (root) { loadList(root, current && current.id); }
  });
})();
