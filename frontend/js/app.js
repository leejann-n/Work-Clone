/* WorkClone 메인 화면 — 순수 JavaScript (라이브러리 없음) */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* ───── 1. 아이콘: <i data-icon="이름"> → SVG ───── */
  function renderIcons(root) {
    $$('i[data-icon]', root).forEach(function (el) {
      var body = window.ICONS && window.ICONS[el.dataset.icon];
      if (!body) return;
      var size = el.dataset.size || 24;
      el.outerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '" viewBox="0 0 24 24"' +
        ' fill="' + (el.dataset.fill || 'none') + '"' +
        ' stroke="' + (el.dataset.stroke || 'currentColor') + '"' +
        ' stroke-width="' + (el.dataset.strokeWidth || 2) + '" stroke-linecap="round" stroke-linejoin="round"' +
        (el.className ? ' class="' + el.className + '"' : '') +
        ' aria-hidden="true">' + body + '</svg>';
    });
  }
  renderIcons();

  // 아이콘을 그렸고 글꼴도 준비되면 화면 표시 (최대 1.2초 기다림) → 새로고침 때 깜빡임 방지
  (function reveal() {
    var done = function () { document.documentElement.classList.remove('booting'); };
    var timer = setTimeout(done, 1200);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { clearTimeout(timer); done(); });
    else done();
  })();

  /* ───── 2. 사이드바 메뉴 / 화면 전환 ───── */
  var pagePlaceholder = $('#page-placeholder');
  // 화면 이름 → 영역. 목록에 없는 메뉴는 '준비 중' 화면
  var PAGES = { home: '#page-home', run: '#page-run', build: '#page-build', my: '#page-my', history: '#page-history' };
  var lastWork = null;   // 진행 중인 작업 화면 ('build' 또는 'run')

  function navigate(page) {
    if (page === 'create') {
      if (lastWork) page = lastWork;                         // 진행 중인 작업으로
      else { page = 'home'; setTimeout(function () { $('#desc').focus(); }, 50); }
    }
    if (page === 'build' || page === 'run') lastWork = page;
    var navKey = (page === 'run' || page === 'build') ? 'create' : page;
    $$('.nav-item').forEach(function (btn) {
      var on = btn.dataset.page === navKey;
      btn.classList.toggle('active', on);
      if (on) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
    });
    Object.keys(PAGES).forEach(function (k) { $(PAGES[k]).hidden = k !== page; });
    pagePlaceholder.hidden = !!PAGES[page];
    // 작업·목록 화면에서는 떠 있는 어시스턴트 버튼이 버튼·표를 가리지 않게 숨김
    document.body.classList.toggle('on-run', ['run', 'build', 'my', 'history'].indexOf(page) >= 0);
    if (!PAGES[page]) {
      var label = $('.nav-item[data-page="' + page + '"] span');
      $('#placeholder-title').textContent = label ? label.textContent : '';
    }
    document.dispatchEvent(new CustomEvent('wc:page', { detail: page }));   // 각 화면이 열릴 때 새로고침
    window.scrollTo(0, 0);
  }
  $$('.nav-item').forEach(function (btn) {
    btn.addEventListener('click', function () { navigate(btn.dataset.page); });
  });
  $('#bell').addEventListener('click', function () { navigate('mail'); });

  /* ───── 3. 빠르게 업무화 만들기: 작업 파일(업무 전·후) + 업무 과정 설명 ───── */
  var slotFiles = { before: [], after: [] };
  var desc = $('#desc');
  var descCount = $('#desc-count');
  var analyzeBtn = $('#analyze');
  var quickStatus = $('#quick-status');

  function addFiles(slot, list) {
    Array.prototype.forEach.call(list || [], function (f) {
      var dup = slotFiles[slot].some(function (x) { return x.name === f.name && x.size === f.size; });
      if (!dup) slotFiles[slot].push(f);
    });
    renderSlot(slot);
  }

  function renderSlot(slot) {
    var el = $('.slot[data-slot="' + slot + '"]');
    var box = $('.slot-files', el);
    var list = slotFiles[slot];
    box.innerHTML = '';
    el.classList.toggle('filled', list.length > 0);
    list.forEach(function (f, i) {
      var chip = document.createElement('span');
      chip.className = 'file-chip';
      chip.textContent = f.name;
      var x = document.createElement('button');
      x.type = 'button';
      x.setAttribute('aria-label', f.name + ' 제거');
      x.innerHTML = '<i data-icon="x" data-size="12"></i>';
      x.addEventListener('click', function () { list.splice(i, 1); renderSlot(slot); });
      chip.appendChild(x);
      box.appendChild(chip);
    });
    renderIcons(box);
    updateQuick();
  }

  // 분석 시작 조건: 업무 전 파일 1개 이상 + 업무 설명 10자 이상
  function updateQuick() {
    var hasBefore = slotFiles.before.length > 0;
    var hasDesc = desc.value.trim().length >= 10;
    descCount.textContent = desc.value.length + ' / 1000';
    analyzeBtn.disabled = !(hasBefore && hasDesc);
    var msg;
    if (!hasBefore && !hasDesc) msg = '업무 전 파일과 업무 설명을 입력해 주세요';
    else if (!hasBefore) msg = '업무 전 파일을 올려 주세요';
    else if (!hasDesc) msg = '업무 과정을 조금 더 자세히 설명해 주세요';
    else if (!slotFiles.after.length) msg = '준비 완료 · 업무 후 파일도 올리면 더 정확해요';
    else msg = '준비 완료';
    quickStatus.textContent = msg;
    quickStatus.classList.toggle('ready', !analyzeBtn.disabled);
  }

  $$('.slot').forEach(function (el) {
    var slot = el.dataset.slot;
    var input = $('input[type="file"]', el);
    $('.slot-pick', el).addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () { addFiles(slot, input.files); input.value = ''; });
    el.addEventListener('dragover', function (e) { e.preventDefault(); el.classList.add('dragging'); });
    el.addEventListener('dragleave', function () { el.classList.remove('dragging'); });
    el.addEventListener('drop', function (e) {
      e.preventDefault();
      el.classList.remove('dragging');
      addFiles(slot, e.dataTransfer.files);
    });
  });

  desc.addEventListener('input', updateQuick);
  $$('.examples button').forEach(function (b) {
    b.addEventListener('click', function () {
      desc.value = b.dataset.example;
      desc.focus();
      updateQuick();
      // 회계 예시: 서버의 샘플 급여대장을 '업무 전 파일'로 함께 첨부
      if (b.dataset.sample === 'payroll' && !slotFiles.before.length) {
        fetch('/api/samples/payroll')
          .then(function (r) { if (!r.ok) throw new Error('sample'); return r.blob(); })
          .then(function (blob) {
            addFiles('before', [new File([blob], '11_급여대장_2026-11.csv', { type: 'text/csv' })]);
          })
          .catch(function () { quickStatus.textContent = '샘플 파일을 불러오지 못했습니다. 서버 실행 여부를 확인하세요.'; });
      }
    });
  });

  analyzeBtn.addEventListener('click', function () {
    if (!window.WCRun) return;
    analyzeBtn.disabled = true;
    quickStatus.textContent = 'AI 작업 화면을 여는 중…';
    window.WCRun.start({ prompt: desc.value.trim(), before: slotFiles.before, after: slotFiles.after })
      .then(function (page) { navigate(page); updateQuick(); })
      .catch(function (err) { updateQuick(); quickStatus.textContent = err.message; });
  });

  /* ───── 5. 내 자동화 현황 그래프 (서버의 실제 실행 기록으로 채움: my.js → setTrend) ───── */
  var TREND = [];

  // 그래프는 상자(.chart-box)의 실제 크기에 맞춰 그립니다 (화면 높이에 따라 늘었다 줄었다 함)
  function drawChart(svg, data) {
    var box = svg.parentNode;
    var W = Math.max(box.clientWidth, 200), H = Math.max(box.clientHeight, 60);
    var padX = 16, padTop = 10, padBottom = 20;
    var max = Math.max.apply(null, data.map(function (d) { return d.value; })) * 1.15;
    var pts = data.map(function (d, i) {
      return {
        x: padX + (i * (W - padX * 2)) / (data.length - 1),
        y: padTop + (1 - d.value / max) * (H - padTop - padBottom),
        label: d.label,
        value: d.value
      };
    });
    var line = pts.map(function (p, i) { return (i ? 'L' : 'M') + p.x + ',' + p.y; }).join(' ');
    var last = pts[pts.length - 1];
    var area = line + ' L' + last.x + ',' + (H - padBottom) + ' L' + pts[0].x + ',' + (H - padBottom) + ' Z';
    var html =
      '<defs><linearGradient id="lc-fill" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#22a86b" stop-opacity=".22"/><stop offset="1" stop-color="#22a86b" stop-opacity="0"/>' +
      '</linearGradient></defs>' +
      '<path d="' + area + '" fill="url(#lc-fill)"/>' +
      '<path d="' + line + '" fill="none" stroke="#22a86b" stroke-width="2" stroke-linejoin="round"/>';
    pts.forEach(function (p) {
      html +=
        '<circle cx="' + p.x + '" cy="' + p.y + '" r="3.2" fill="#fff" stroke="#22a86b" stroke-width="1.8">' +
        '<title>' + p.label + ' · ' + p.value + '회</title></circle>' +
        '<text x="' + p.x + '" y="' + (H - 6) + '" text-anchor="middle" class="axis">' + p.label + '</text>';
    });
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.innerHTML = html;
  }
  var chartSvg = $('#trend-chart');
  function redraw() {
    if (!TREND.length) {
      var box = chartSvg.parentNode;
      chartSvg.setAttribute('viewBox', '0 0 ' + Math.max(box.clientWidth, 200) + ' ' + Math.max(box.clientHeight, 60));
      chartSvg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" class="axis" style="font-size:12px">자동화를 실행하면 일별 실행 횟수가 표시됩니다</text>';
      return;
    }
    drawChart(chartSvg, TREND.length === 1 ? [TREND[0], TREND[0]] : TREND);
  }
  redraw();
  if (window.ResizeObserver) new ResizeObserver(redraw).observe(chartSvg.parentNode);
  // 서버의 실제 실행 기록으로 그래프 교체 (my.js에서 호출)
  function setTrend(data) { TREND = data; redraw(); }

  // run.js 등 다른 스크립트에서 쓰는 공용 함수
  // 예시(템플릿)를 '빠르게 업무화 만들기' 칸에 채우기
  function fillQuick(opts) {
    slotFiles.before = [];
    slotFiles.after = [];
    addFiles('before', opts.before || []);
    addFiles('after', opts.after || []);
    desc.value = opts.prompt || '';
    updateQuick();
    navigate('home');
    desc.focus();
  }

  window.WC = { navigate: navigate, renderIcons: renderIcons, fillQuick: fillQuick, setTrend: setTrend,
                addPage: function (name) { PAGES[name] = '#page-' + name; } };   // 팀원 기능 화면 등록 (js/features.js)

  /* ───── 6. AI 어시스턴트 (UI 목업 — 서버 연동 전) ───── */
  var assistant = $('#assistant');
  var asBody = $('#as-body');
  var asForm = $('#as-form');
  var asInput = $('#as-input');
  var asSend = $('button[type="submit"]', asForm);

  function bubble(role, text) {
    var div = document.createElement('div');
    div.className = 'bubble ' + role;
    div.textContent = text;
    asBody.appendChild(div);
    asBody.scrollTop = asBody.scrollHeight;
  }

  function send(text) {
    text = text.trim();
    if (!text) return;
    var sugs = $('#as-sugs');
    if (sugs) sugs.hidden = true;
    bubble('user', text);
    bubble('ai', '아직 서버와 연결되지 않았습니다. 연동 후에는 출처와 함께 답변하고, 데이터를 바꾸는 작업은 실행 전에 확인을 받습니다.');
    asInput.value = '';
    asSend.disabled = true;
  }

  $('#fab').addEventListener('click', function () {
    assistant.hidden = !assistant.hidden;
    if (!assistant.hidden) asInput.focus();
  });
  $('#assistant-close').addEventListener('click', function () { assistant.hidden = true; });
  $$('#as-sugs button').forEach(function (b) {
    b.addEventListener('click', function () { send(b.textContent); });
  });
  asInput.addEventListener('input', function () { asSend.disabled = !asInput.value.trim(); });
  asForm.addEventListener('submit', function (e) { e.preventDefault(); send(asInput.value); });
})();
