/* 화면 공용 도구: 글자 이스케이프, 간단한 Markdown, 표 그리기, 서버 통신 */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmt(v) {
    if (v == null) return '';
    if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString('ko-KR') : v.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
    return String(v);
  }

  function md(text) {
    var lines = esc(text).split('\n'), html = '', i = 0;
    function inline(s) {
      return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
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

  // preview: {columns, rows, total_rows}, marks: {"행번호|열": "cls"}
  function previewTable(preview, opts) {
    opts = opts || {};
    var cols = preview.columns || [];
    var body = (preview.rows || []).map(function (r, ri) {
      return '<tr>' + r.map(function (v, ci) {
        var cls = (typeof v === 'number' ? 'num' : '') + (opts.marks && opts.marks[ri + '|' + cols[ci]] ? ' ' + opts.marks[ri + '|' + cols[ci]] : '');
        return '<td class="' + cls + '">' + esc(fmt(v)) + '</td>';
      }).join('') + '</tr>';
    }).join('');
    var more = preview.total_rows > (preview.rows || []).length ? '<p class="tbl-note">전체 ' + fmt(preview.total_rows) + '행 중 ' + preview.rows.length + '행 표시</p>' : '';
    return '<div class="tbl-wrap"><table class="tbl"><thead><tr>' + cols.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + body + '</tbody></table></div>' + more;
  }

  function json(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.detail || ('서버 오류 (' + r.status + ')'));
        return j;
      });
    }, function () { throw new Error('서버에 연결할 수 없습니다.'); });
  }

  // NDJSON 스트림: 줄마다 onEvent(ev)
  function stream(url, body, onEvent) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (res) {
        if (!res.ok || !res.body) {
          return res.json().catch(function () { return {}; }).then(function (j) {
            throw new Error(j.detail || ('서버 응답 오류 (' + res.status + ')'));
          });
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
              try { onEvent(JSON.parse(line)); } catch (e) { /* 깨진 줄 무시 */ }
            });
            return pump();
          });
        }
        return pump();
      });
  }

  function timeAgo(iso) {
    if (!iso) return '';
    var d = (Date.now() - new Date(iso).getTime()) / 1000;
    if (d < 60) return '방금 전';
    if (d < 3600) return Math.floor(d / 60) + '분 전';
    if (d < 86400) return Math.floor(d / 3600) + '시간 전';
    return iso.slice(0, 10);
  }

  /* 채팅 입력칸: Enter = 보내기, Shift+Enter = 줄바꿈, 한글 입력 중 Enter는 무시, 내용에 맞춰 높이 조절 */
  function chatInput(ta, form) {
    function fit() {
      ta.style.height = 'auto';
      if (ta.value) ta.style.height = Math.min(ta.scrollHeight + 2, 140) + 'px';   // 비어 있으면 한 줄 (안내 문구 길이와 무관)
    }
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      if (form.requestSubmit) form.requestSubmit(); else form.dispatchEvent(new Event('submit', { cancelable: true }));
    });
    form.addEventListener('submit', function () { setTimeout(fit, 0); });
    fit();
    return { fit: fit };
  }

  window.WCU = { esc: esc, fmt: fmt, md: md, previewTable: previewTable, json: json, stream: stream, timeAgo: timeAgo, chatInput: chatInput };
})();
