/* 회의 분석 (담당: 팀원 1) */
(function () {
  var U = window.WCU, api = WCFeature.api;
  function el(root, n) { return root.querySelector('[data-el="' + n + '"]'); }

  function render(m) {
    var items = (m.action_items || []).map(function (a) {
      return '<li>' + (a.done ? '✅ ' : '⬜ ') + U.esc(a.task) + ' — ' + U.esc(a.assignee || '') + (a.due ? ' (' + U.esc(a.due) + ')' : '') + '</li>';
    }).join('');
    return '<h3>' + U.esc(m.title) + '</h3><p style="color:var(--muted)">' + U.esc(m.held_at || '') + ' · ' + U.esc((m.participants || []).join(', ')) + '</p>' +
      '<h4>요약</h4><p>' + U.esc(m.summary || '(아직 분석 전)') + '</p>' +
      '<h4>결정 사항</h4><ul>' + (m.decisions || []).map(function (d) { return '<li>' + U.esc(d) + '</li>'; }).join('') + '</ul>' +
      '<h4>할 일</h4><ul>' + items + '</ul>';
  }

  function loadList(root) {
    api('/api/meeting/list').then(function (rows) {
      el(root, 'list').innerHTML = rows.map(function (m) {
        return '<li data-id="' + U.esc(m.id) + '">' + U.esc(m.title) + '<small>' + U.esc(m.held_at) + (m.summary ? ' · 분석됨' : '') + '</small></li>';
      }).join('') || '<li class="empty">회의록이 없습니다.</li>';
    }).catch(function (e) { el(root, 'list').innerHTML = '<li class="fp-error">' + U.esc(e.message) + '</li>'; });
  }

  WCFeature.register('meeting', {
    init: function (root) {
      el(root, 'date').value = new Date().toISOString().slice(0, 10);
      el(root, 'list').addEventListener('click', function (e) {
        var li = e.target.closest('li[data-id]');
        if (!li) return;
        api('/api/meeting/' + encodeURIComponent(li.dataset.id)).then(function (m) { el(root, 'result').innerHTML = render(m); });
      });
      el(root, 'go').addEventListener('click', function () {
        var btn = this;
        btn.disabled = true; el(root, 'err').textContent = 'AI 분석 중…';
        api('/api/meeting/analyze', { body: {
          title: el(root, 'title').value || '제목 없는 회의', held_at: el(root, 'date').value || null,
          participants: el(root, 'people').value.split(',').map(function (s) { return s.trim(); }).filter(Boolean),
          transcript: el(root, 'text').value } })
          .then(function (m) { el(root, 'err').textContent = ''; el(root, 'result').innerHTML = render(m); loadList(root); })
          .catch(function (e) { el(root, 'err').textContent = e.message; })
          .then(function () { btn.disabled = false; });
      });
    },
    show: function (root) { loadList(root); }
  });
})();
