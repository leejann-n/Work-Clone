/* 로그인 · 로그인 정보 유지 · 상단 프로필 · 파일 내려받기 · AI 사용 횟수 표시 */
(function () {
  'use strict';

  var KEY = 'wc_session';
  var $ = function (s) { return document.querySelector(s); };
  var session = null;
  try { session = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { session = null; }
  var authOn = null;            // 서버가 로그인 기능을 쓰는지 (/api/config)
  var refreshing = null;
  var readyResolve;
  var ready = new Promise(function (r) { readyResolve = r; });

  function save(s) {
    session = s;
    try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY); } catch (e) { /* 저장 불가해도 이번 화면에서는 동작 */ }
  }

  /* ───── 모든 /api 요청에 로그인 토큰 붙이기 (기존 화면 코드는 그대로) ───── */
  var PUBLIC = /^\/api\/(auth\/login|auth\/refresh|config|health|samples)/;
  var origFetch = window.fetch.bind(window);

  function withAuth(input, init) {
    init = Object.assign({}, init || {});
    var headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    if (session && session.access_token) headers.set('Authorization', 'Bearer ' + session.access_token);
    init.headers = headers;
    return init;
  }

  function doRefresh() {
    if (!session || !session.refresh_token) return Promise.reject(new Error('no session'));
    if (!refreshing) {
      refreshing = origFetch('/api/auth/refresh', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: session.refresh_token })
      }).then(function (r) {
        if (!r.ok) throw new Error('refresh failed');
        return r.json();
      }).then(function (t) {
        save(Object.assign({}, session, t));
      }).finally(function () { refreshing = null; });
    }
    return refreshing;
  }

  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : input.url;
    var path = url.replace(location.origin, '');
    if (path.indexOf('/api/') !== 0 || PUBLIC.test(path) || !authOn) return origFetch(input, init);
    // 만료 1분 전이면 미리 갱신
    var pre = session && session.expires_at && session.expires_at * 1000 - Date.now() < 60000 ? doRefresh().catch(function () {}) : Promise.resolve();
    return pre.then(function () {
      return origFetch(input, withAuth(input, init));
    }).then(function (res) {
      if (res.status !== 401) return res;
      return doRefresh().then(function () {
        return origFetch(input, withAuth(input, init));
      }, function () {
        showLogin('로그인이 만료되었습니다. 다시 로그인해 주세요.');
        return res;
      });
    });
  };

  /* ───── 파일 내려받기 (링크에는 토큰을 붙일 수 없어 fetch로 받아 저장) ───── */
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[download][href^="/api/"]');
    if (!a || !authOn) return;
    e.preventDefault();
    window.fetch(a.getAttribute('href')).then(function (r) {
      if (!r.ok) throw new Error('내려받기 실패 (' + r.status + ')');
      var cd = r.headers.get('Content-Disposition') || '';
      var m = cd.match(/filename\*=UTF-8''([^;]+)/);
      var name = m ? decodeURIComponent(m[1]) : (a.getAttribute('download') || decodeURIComponent(a.getAttribute('href').split('/').pop()));
      return r.blob().then(function (b) {
        var u = URL.createObjectURL(b), tmp = document.createElement('a');
        tmp.href = u; tmp.download = name;
        document.body.appendChild(tmp); tmp.click(); tmp.remove();
        setTimeout(function () { URL.revokeObjectURL(u); }, 2000);
      });
    }).catch(function (err) { alert(err.message); });
  });

  /* ───── 화면 전환 ───── */
  function showLogin(msg) {
    save(null);
    document.documentElement.classList.remove('auth-pending');
    $('#login').hidden = false;
    document.body.classList.add('on-login');
    var err = $('#login-error');
    err.hidden = !msg;
    err.textContent = msg || '';
    setTimeout(function () { $('#login-id').focus(); }, 50);
  }

  function showApp(emp, usage) {
    $('#login').hidden = true;
    document.body.classList.remove('on-login');
    document.documentElement.classList.remove('auth-pending');
    setProfile(emp);
    setUsage(usage);
    readyResolve();
    document.dispatchEvent(new CustomEvent('wc:login', { detail: emp }));
  }

  function setProfile(emp) {
    emp = emp || {};
    $('#profile-name').textContent = (emp.name || '로컬 사용자') + (emp.position ? ' ' + emp.position : '');
    $('#profile-team').textContent = emp.department || '로그인 없이 사용 중';
    $('#profile-avatar').textContent = emp.name ? emp.name.charAt(0) : '?';
    $('#pm-info').innerHTML = emp.emp_no
      ? '<b>' + WCU.esc(emp.name) + '</b><span>' + WCU.esc(emp.department) + ' · ' + WCU.esc(emp.position) + '</span><span>사번 ' +
        WCU.esc(emp.emp_no) + ' · ' + WCU.esc(emp.email || '') + '</span>'
      : '<b>로컬 모드</b><span>로그인 기능이 꺼져 있습니다</span>';
    $('#logout-btn').hidden = !authOn;
  }

  function setUsage(u) {
    var chip = $('#usage-chip');
    if (!u || u.limit == null) { chip.hidden = true; return; }
    chip.hidden = false;
    chip.textContent = 'AI ' + u.used + ' / ' + u.limit;
    chip.classList.toggle('warn', u.used >= u.limit * 0.8);
  }

  /* ───── 로그인 폼 ───── */
  $('#login-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = $('#login-submit'), err = $('#login-error');
    btn.disabled = true;
    btn.textContent = '로그인 중…';
    err.hidden = true;
    origFetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emp_no: $('#login-id').value.trim(), password: $('#login-pw').value })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.detail || '로그인하지 못했습니다.');
        return j;
      });
    }, function () { throw new Error('서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요 (처음 접속 시 최대 1분).'); })
      .then(function (j) {
        save({ access_token: j.access_token, refresh_token: j.refresh_token, expires_at: j.expires_at, employee: j.employee });
        $('#login-pw').value = '';
        showApp(j.employee, j.usage);
      })
      .catch(function (ex) { err.hidden = false; err.textContent = ex.message; })
      .then(function () { btn.disabled = false; btn.textContent = '로그인'; });
  });

  /* ───── 프로필 메뉴 · 로그아웃 ───── */
  $('#profile-btn').addEventListener('click', function (e) {
    e.stopPropagation();
    var m = $('#profile-menu');
    m.hidden = !m.hidden;
    this.setAttribute('aria-expanded', String(!m.hidden));
  });
  document.addEventListener('click', function () { $('#profile-menu').hidden = true; });
  $('#logout-btn').addEventListener('click', function () {
    window.fetch('/api/auth/logout', { method: 'POST' }).catch(function () {}).then(function () {
      showLogin('');
      location.hash = '';
      setTimeout(function () { location.reload(); }, 50);   // 화면 상태 초기화
    });
  });

  /* ───── 비밀번호 보기/숨기기 ───── */
  $('#pw-toggle').addEventListener('click', function () {
    var pw = $('#login-pw'), show = pw.type === 'password';
    pw.type = show ? 'text' : 'password';
    this.setAttribute('aria-label', show ? '비밀번호 숨기기' : '비밀번호 보기');
    this.innerHTML = '<i data-icon="' + (show ? 'eye-off' : 'eye') + '" data-size="20"></i>';
    if (window.WC) window.WC.renderIcons(this);
    pw.focus();
  });

  /* ───── 시작: 서버 설정 확인 → 로그인 상태 확인 ───── */
  function start() {
    if (location.protocol === 'file:') { authOn = false; showApp(null, null); return; }
    var tries = 0;
    (function check() {
      origFetch('/api/config').then(function (r) {
        if (r.status === 404) throw new Error('old');        // 서버가 예전 버전 (로그인 기능 없음)
        if (!r.ok) throw new Error('down');
        return r.json();
      }).then(function (cfg) {
        authOn = !!cfg.auth;
        var isLocal = /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
        if (!authOn && isLocal) { showApp(null, null); return; }     // 내 컴퓨터에서 개발할 때만 로그인 없이
        if (!authOn) {
          authOn = true;
          showLogin('서버에 로그인 설정이 없습니다. Render 환경 변수 SUPABASE_URL·SUPABASE_PUBLISHABLE_KEY를 확인해 주세요.');
          return;
        }
        if (!session) { showLogin(''); return; }
        window.fetch('/api/auth/me').then(function (r) {
          if (!r.ok) throw new Error();
          return r.json();
        }).then(function (j) { showApp(j.employee, j.usage); }, function () { showLogin(''); });
      }).catch(function (e) {
        if (e && e.message === 'old') {
          authOn = true;
          showLogin('서버가 아직 예전 버전입니다. Render에서 최신 코드로 배포되었는지 확인해 주세요.');
          return;
        }
        // 서버가 깨어나는 중 (Render 무료) → 잠시 후 다시
        var msg = document.getElementById('boot-wait-msg');
        if (msg) msg.textContent = '서버를 깨우는 중입니다. 무료 서버라 처음 접속하면 최대 1분쯤 걸립니다. (' + (tries + 1) + '/12)';
        if (tries++ < 12) setTimeout(check, 5000); else showLogin('서버에 연결할 수 없습니다. Render 대시보드에서 서버 상태(Events·Logs)를 확인해 주세요.');
      });
    })();
  }

  window.WCAuth = { ready: ready, setUsage: setUsage, employee: function () { return session && session.employee; },
                    enabled: function () { return authOn; } };
  document.addEventListener('DOMContentLoaded', start);
})();
