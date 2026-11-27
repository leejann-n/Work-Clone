"""규정·회의록 검색 (RAG의 '검색' 부분).

외부 라이브러리 없이 동작하는 BM25 검색입니다.
- 문서를 Markdown 제목(##, ###) 단위로 잘라 '조각(chunk)'으로 만듭니다.
- 한국어는 띄어쓰기만으로 단어를 나누기 어려워서, 글자 2개 단위(bigram)와 단어를 함께 색인합니다.
- 나중에 Supabase(pgvector) 임베딩 검색으로 바꿀 때는 search() 함수만 교체하면 됩니다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

RECENT_DAYS = 14
RECENT_BOOST = 1.6


def _is_recent(doc_date: str, today: date) -> bool:
    """규정(시행일)이 아니라 회의·메모처럼 날짜가 있는 문서 중 최근 2주 이내인지"""
    try:
        d = date.fromisoformat(doc_date)
    except ValueError:
        return False
    return today - timedelta(days=RECENT_DAYS) <= d <= today

# 업무 용어 동의어: 검색어에 왼쪽 단어가 있으면 오른쪽 단어도 함께 찾습니다.
SYNONYMS = {
    "야근": ["야간", "연장근로", "시간외"],
    "야간연장": ["야간", "연장", "중복"],
    "입사자": ["신규", "입사", "일할"],
    "신입": ["신규", "입사자"],
    "일할": ["역일수", "근무 일수"],
    "성과급": ["성과", "평가 등급", "지급률"],
    "이체": ["승인", "이체 파일"],
    "점검": ["사번 중복", "인원 수", "총액 대조"],
    "전월": ["10월", "지난달"],
}


MAX_CHUNK_CHARS = 450


@dataclass
class Chunk:
    id: str           # 예: "01_급여규정#제6조"
    doc: str          # 파일 이름(확장자 제외)
    title: str        # 문서 제목 (# 줄)
    section: str      # 조각의 제목 경로
    date: str         # 문서 날짜 (파일명/본문에서 추출, 없으면 "")
    text: str
    tokens: Counter = field(default_factory=Counter)
    length: int = 0


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[0-9a-z가-힣]+", text)
    out: list[str] = []
    for w in words:
        if len(w) >= 2:
            out.append(w)
        if re.search(r"[가-힣]", w) and len(w) > 2:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


def _doc_date(name: str, body: str) -> str:
    m = re.search(r"(20\d\d-\d\d-\d\d)", name) or re.search(r"일시:\s*(20\d\d-\d\d-\d\d)", body)
    if m:
        return m.group(1)
    m = re.search(r"(?:시행일|작성일):\s*(20\d\d-\d\d-\d\d)", body)
    return m.group(1) if m else ""


def split_markdown(name: str, body: str) -> list[Chunk]:
    title_m = re.search(r"^#\s+(.+)$", body, re.M)
    title = title_m.group(1).strip() if title_m else name
    date = _doc_date(name, body)
    chunks: list[Chunk] = []
    h2 = ""
    current_head = "개요"
    buf: list[str] = []

    def flush():
        text = "\n".join(buf).strip()
        if not text:
            return
        section = current_head if not h2 or h2 == current_head else f"{h2} > {current_head}"
        # 제목 없이 긴 본문(예: 회의 녹취)은 문단 단위로 묶어 400자 안팎으로 나눕니다.
        parts: list[str] = []
        if len(text) > MAX_CHUNK_CHARS:
            cur = ""
            for para in re.split(r"\n\s*\n", text):
                if cur and len(cur) + len(para) > MAX_CHUNK_CHARS:
                    parts.append(cur)
                    cur = ""
                cur = f"{cur}\n\n{para}".strip()
            if cur:
                parts.append(cur)
        else:
            parts = [text]
        for i, part in enumerate(parts, start=1):
            suffix = f" ({i}/{len(parts)})" if len(parts) > 1 else ""
            chunks.append(Chunk(id=f"{name}#{current_head}{suffix}", doc=name, title=title,
                                section=section + suffix, date=date, text=part))

    for line in body.splitlines():
        m2 = re.match(r"^##\s+(.+)$", line)
        m3 = re.match(r"^###\s+(.+)$", line)
        if m2 or m3:
            flush()
            buf = []
            if m2:
                h2 = m2.group(1).strip()
                current_head = h2
            else:
                current_head = m3.group(1).strip()
            continue
        if line.startswith("# "):
            continue
        buf.append(line)
    flush()
    return chunks


class DocIndex:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self._df: Counter = Counter()
        self._avg = 1.0

    def add_document(self, name: str, body: str) -> None:
        for c in split_markdown(name, body):
            c.tokens = Counter(_tokenize(f"{c.title} {c.section} {c.text}"))
            c.length = sum(c.tokens.values())
            self.chunks.append(c)
        self._reindex()

    def add_folder(self, folder: Path) -> None:
        for p in sorted(folder.glob("*.md")):
            self.add_document(p.stem, p.read_text(encoding="utf-8"))

    def _reindex(self) -> None:
        self._df = Counter()
        for c in self.chunks:
            self._df.update(set(c.tokens))
        self._avg = sum(c.length for c in self.chunks) / max(len(self.chunks), 1)

    def search(self, query: str, top_k: int = 4, today: date | None = None) -> list[dict]:
        today = today or date.today()
        top_k = max(top_k, 3)   # 너무 적게 찾으면 최근 회의 같은 근거를 놓치기 쉬움
        terms = _tokenize(query)
        for key, extra in SYNONYMS.items():
            if key in query:
                for e in extra:
                    terms.extend(_tokenize(e))
        n = len(self.chunks)
        k1, b = 1.4, 0.75
        scored = []
        for c in self.chunks:
            s = 0.0
            for t in set(terms):
                tf = c.tokens.get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * c.length / self._avg))
            if s > 0:
                if _is_recent(c.date, today):
                    s *= RECENT_BOOST   # 최근 2주 안의 회의·메모는 최신 지시일 수 있어 우선
                scored.append((s, c))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return []
        best = scored[0][0]
        results = []
        per_doc: Counter = Counter()
        for s, c in scored:
            if len(results) >= top_k or s < best * 0.35:   # 1등 대비 너무 약한 결과는 근거로 쓰지 않음
                break
            if per_doc[c.doc] >= 2:    # 한 문서가 결과를 독차지하지 않도록 문서당 최대 2개
                continue
            per_doc[c.doc] += 1
            results.append({
                "source_id": c.id,
                "document": c.title,
                "file": c.doc,
                "section": c.section,
                "date": c.date,
                "recent": _is_recent(c.date, today),
                "score": round(s, 2),
                "text": c.text[:700],
            })
        return results
