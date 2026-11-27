/* 팀원 기능 화면 자동 연결
 * 사이드바 메뉴(data-page="meeting" 등)를 누르면 frontend/features/<이름>/ 의
 *   view.html (화면 조각) · view.css · view.js 를 불러와 붙입니다.
 * view.js 에서는 이렇게 등록합니다:
 *   WCFeature.register('meeting', {
 *     init: function (root) { ... 처음 한 번 },
 *     show: function (root) { ... 메뉴를 누를 때마다 (새로고침) }
 *   });
 * 공통 도구: WCFeature.api(경로, {method, body}) → JSON (로그인 토큰 자동, 오류는 Error로)
 *           WCU.esc(글자) 안전한 HTML, WCU.fmt(숫자) 쉼표, WCU.md(마크다운) → HTML, WCU.timeAgo(시간)
 * 이 파일은 공통 파일입니다. 고쳐야 하면 PM에게 요청해 주세요.
 */
(function () {
  'use strict';

  var mods = {}, state = {};   // state[name]: 'loading' | 'ready' | 'missing'
  var main = document.querySelector('main.content');

  function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || (opts.body ? 'POST' : 'GET'), headers: {} };
    if (opts.body instanceof FormData) init.body = opts.body;
    else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers['Content-Type'] = 'application/json'; }
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error((j && j.detail) || ('요청 실패 (' + r.status + ')'));
        return j;
      });
    });
  }

  function addCss(name) {
    var l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = 'features/' + name + '/view.css';
    document.head.appendChild(l);
  }

  function addJs(name) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = 'features/' + name + '/view.js';
      s.onload = resolve;
      s.onerror = function () { reject(new Error('view.js를 불러오지 못했습니다')); };
      document.body.appendChild(s);
    });
  }

  function load(name) {
    state[name] = 'loading';
    return fetch('features/' + name + '/view.html').then(function (r) {
      if (!r.ok) throw new Error('missing');
      return r.text();
    }).then(function (html) {
      var root = document.createElement('div');
      root.className = 'mypage feature-page';
      root.id = 'page-' + name;
      root.hidden = true;
      root.innerHTML = html;
      main.appendChild(root);
      addCss(name);
      return addJs(name).then(function () {
        state[name] = 'ready';
        window.WC.addPage(name);
        if (window.WC.renderIcons) window.WC.renderIcons(root);
        return true;
      });
    }).catch(function (e) {
      state[name] = 'missing';
      if (e.message !== 'missing') console.error('[기능 ' + name + ']', e);
      return false;
    });
  }

  function showFeature(name) {
    var root = document.getElementById('page-' + name);
    var m = mods[name];
    if (!root || !m) return;
    document.body.classList.add('on-run');
    var ready = window.WCAuth ? window.WCAuth.ready : Promise.resolve();
    ready.then(function () {
      try {
        if (!m._inited) { m._inited = true; if (m.init) m.init(root); }
        if (m.show) m.show(root);
      } catch (e) { console.error('[기능 ' + name + ']', e); }
    });
  }

  // 화면 전환 때마다: 처음 보는 메뉴면 불러오고, 이미 있으면 show 호출
  document.addEventListener('wc:page', function (e) {
    var name = e.detail;
    if (state[name] === 'ready') { showFeature(name); return; }
    if (state[name] || !document.querySelector('.nav-item[data-page="' + name + '"]')) return;
    load(name).then(function (ok) {
      if (ok && document.querySelector('.nav-item[data-page="' + name + '"].active')) window.WC.navigate(name);
    });
  });

  // 로그인하면 업무 메일의 안 읽은 메일 수를 사이드바에 표시
  document.addEventListener('wc:login', function () {
    var badge = document.querySelector('.nav-item[data-page="mail"] .nav-badge');
    if (!badge) return;
    api('/api/mail/unread').then(function (r) { badge.textContent = r.unread; badge.style.display = r.unread ? '' : 'none'; })
      .catch(function () { badge.style.display = 'none'; });
  });

  window.WCFeature = {
    register: function (name, mod) { mods[name] = mod || {}; },
    api: api,
    loaded: function () { return Object.keys(state).filter(function (k) { return state[k] === 'ready'; }); }
  };
})();
