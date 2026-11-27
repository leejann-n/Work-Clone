/* 회의록 검색 (담당: 팀원 2) */
(function () {
  var U = window.WCU, api = WCFeature.api;
  function el(root, n) { return root.querySelector('[data-el="' + n + '"]'); }

  function run(root) {
    var q = el(root, 'q').value.trim();
    if (!q) return;
    el(root, 'out').innerHTML = '<p class="empty">찾는 중…</p>';
    api('/api/search?q=' + encodeURIComponent(q)).then(function (r) {
      el(root, 'out').innerHTML = r.hits.map(function (h) {
        return '<div class="card" style="padding:12px;margin-bottom:8px"><b>' + U.esc(h.title || h.document) + '</b> <small style="color:var(--muted)">' +
          U.esc(h.held_at || '') + ' · 점수 ' + h.score + '</small><p style="white-space:pre-wrap;margin:6px 0 0">' + U.esc(h.text) + '</p></div>';
      }).join('') || '<p class="empty">찾은 내용이 없습니다.</p>';
    }).catch(function (e) { el(root, 'out').innerHTML = '<p class="fp-error">' + U.esc(e.message) + '</p>'; });
  }

  WCFeature.register('search', {
    init: function (root) {
      el(root, 'go').addEventListener('click', function () { run(root); });
      el(root, 'q').addEventListener('keydown', function (e) { if (e.key === 'Enter') run(root); });
    },
    show: function (root) { el(root, 'q').focus(); }
  });
})();
