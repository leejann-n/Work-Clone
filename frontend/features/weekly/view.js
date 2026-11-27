/* 주간 업무보고 (담당: 팀원 3) */
(function () {
  var U = window.WCU, api = WCFeature.api;
  function el(root, n) { return root.querySelector('[data-el="' + n + '"]'); }

  function sources(root) {
    api('/api/weekly/sources?week_start=' + el(root, 'week').value).then(function (s) {
      el(root, 'src').textContent = s.week_start + ' ~ ' + s.week_end + ' · 자동화 실행 ' + s.runs.length + '건 · 회의 ' + s.meetings.length + '건 · 메일 ' + s.mails.length + '통';
    });
  }

  WCFeature.register('weekly', {
    init: function (root) {
      el(root, 'week').value = new Date().toISOString().slice(0, 10);
      el(root, 'week').addEventListener('change', function () { sources(root); });
      el(root, 'go').addEventListener('click', function () {
        var btn = this;
        btn.disabled = true; el(root, 'err').textContent = 'AI 작성 중…';
        api('/api/weekly/draft', { body: { week_start: el(root, 'week').value } })
          .then(function (r) { el(root, 'err').textContent = ''; el(root, 'draft').innerHTML = U.md(r.content || ''); })
          .catch(function (e) { el(root, 'err').textContent = e.message; })
          .then(function () { btn.disabled = false; });
      });
    },
    show: function (root) { sources(root); }
  });
})();
