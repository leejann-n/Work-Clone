/* 오늘 브리핑 (담당: 팀원 3) */
(function () {
  var U = window.WCU, api = WCFeature.api;

  function card(title, items) {
    return '<section class="card" style="padding:16px"><h3 style="margin-top:0">' + title + '</h3>' +
      (items.length ? '<ul class="fp-list">' + items.join('') + '</ul>' : '<p class="empty">없음</p>') + '</section>';
  }

  WCFeature.register('briefing', {
    show: function (root) {
      var box = root.querySelector('[data-el="cards"]');
      api('/api/briefing/today').then(function (b) {
        box.innerHTML =
          card('안 읽은 메일 ' + b.unread_mails.length + '통', b.unread_mails.map(function (m) {
            return '<li>' + U.esc(m.subject) + '<small>' + U.esc(m.from_name) + '</small></li>'; })) +
          card('내 할 일 ' + b.todos.length + '개', b.todos.map(function (t) {
            return '<li' + (t.overdue ? ' style="border-color:#f04438"' : '') + '>' + U.esc(t.task) + '<small>' + U.esc(t.meeting) +
              (t.due ? ' · 기한 ' + U.esc(t.due) : '') + (t.overdue ? ' · 기한 지남' : '') + '</small></li>'; })) +
          card('오늘 자동화 실행 ' + b.runs_today.length + '건', b.runs_today.map(function (r) {
            return '<li>' + U.esc(r.automation) + '<small>' + U.esc(r.status) + ' · ' + U.esc(r.run_by_name || '') + '</small></li>'; }));
      }).catch(function (e) { box.innerHTML = '<p class="fp-error">' + U.esc(e.message) + '</p>'; });
    }
  });
})();
