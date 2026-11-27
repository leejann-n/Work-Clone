"""업무 자동화 만들기 에이전트 (엑셀).

사용자가 올린 업무 전 파일 / 업무 후 파일 / 업무 설명을 보고
AI가 작업 블록을 조립해 '자동화 레시피'를 만들고, 업무 후 파일과 셀 단위로 맞춰 보며 고칩니다.
모호한 부분은 사용자에게 질문하고, 100% 일치하면 사용자 승인(저장)을 요청합니다.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Iterator

import pandas as pd

from engine import compare as cmp
from engine import recipe as rc
from engine import docgen, pptgen
from engine import tables as tb
from engine.expr import FUNC_HELP
from engine.ops import OPS, ops_help
from llm import ToolLoop, detect_provider, model_name
from rag import DocIndex

BASE = Path(__file__).parent
OUT_DIR = BASE / "output"
TABLE_EXT = (".xlsx", ".xlsm", ".csv", ".pdf")
DOC_EXT = (".md", ".txt")
WORD_EXT = (".docx",)
PPT_EXT = (".pptx",)

SYSTEM = """당신은 WorkClone의 업무 자동화 설계 AI입니다. 오늘은 {today}입니다.
직원이 한 번 처리한 업무 예시(업무 전 파일 → 업무 후 파일)와 설명을 보고,
다음 달에도 새 업무 전 파일만 넣으면 똑같이 처리되는 '자동화 레시피'를 만듭니다.

## 진행 방법
1. inspect_files로 파일 구조를 봅니다. 필요하면 preview_table로 실제 행을 봅니다.
2. 업무 전과 업무 후를 비교해 규칙을 추론합니다: 빠진 행(필터), 묶음 기준과 집계, 계산 열, 정렬 순서,
   합계 행, 열 이름·순서. 업무 후 파일의 열 이름·순서·시트 이름을 정확히 맞춥니다.
3. test_recipe로 레시피를 실행하면 업무 후 파일과 셀 단위로 비교한 결과가 나옵니다.
   일치율 100%이고 perfect=true가 될 때까지 차이(diffs)를 보고 고칩니다.
4. 규칙이 두 가지 이상으로 해석되거나, 고쳐도 설명되지 않는 차이가 남으면 ask_user로 짧게 묻습니다.
   (선택지를 2~4개 주세요. 추측으로 숫자를 맞추지 마세요.)
5. perfect=true가 되면 propose_automation으로 사용자에게 저장 승인을 요청합니다.
   업무 후 파일이 없으면 결과 미리보기를 확인시키는 질문(ask_user)을 한 뒤 제안합니다.

## 꼭 지킬 것
- 레시피는 다음 달 파일에도 맞아야 합니다. 특정 날짜·금액·행 번호를 박아 넣지 말고
  데이터에서 계산하세요 (예: 기준일 = month_end(col_max(청구일))).
  실행할 때마다 바뀌는 값(예: 지급월)은 recipe의 params로 만들고 계산식에서 이름으로 씁니다.
- 숫자를 직접 계산해 레시피에 적지 마세요. 계산은 블록과 계산식이 합니다.
- 업무 후 파일에 있는 '합계' 같은 요약 행도 블록(add_total_row 등)으로 만듭니다.
- PDF에서 뽑은 표: 같은 양식 PDF 여러 장은 표 하나로 합쳐져 있고, 문서 위쪽의 '항목: 값'(문서번호·거래일자·공급자 등)은
  열로 붙어 있으며 '원본파일' 열에 PDF 이름이 있습니다. 명세서 맨 아래 '합계' 줄 같은 요약 행도 그대로 들어 있으니
  필요하면 filter_rows로 빼고, 글자로 읽힌 숫자 열(예: 순번 No)은 convert_type으로 숫자로 바꾼 뒤 정렬하세요.
- 업무 후 파일에 없는 열도 중간 단계의 정렬·계산·연결에 쓸 수 있습니다. 순서: 계산 → 정렬(sort_rows) → 마지막에
  select_columns 또는 outputs의 "columns"로 열을 고르기. 열이 결과에 없다는 이유로 정렬 기준을 사용자에게 묻지 마세요.
  설명에 나온 기준(예: '표 안의 No 순서')은 원본 열 그대로 쓰면 됩니다.
- 대화는 한국어로 짧게 합니다. 도구 결과를 길게 반복하지 않습니다.

## 레시피 형식 (test_recipe의 recipe_json 문자열)
{{"params": [{{"name": "지급월", "description": "급여 지급 월", "default": 11}}],   ← 필요할 때만
 "inputs": [{{"table": "<inspect_files의 업무 전 표 이름>", "columns": ["레시피가 쓰는 열", ...]}}],
 "steps": [{{"op": "<블록>", "params": {{... , "as": "결과 표 이름"}}, "note": "이 단계를 한 줄로 설명"}}],
 "outputs": [{{"table": "<최종 표>", "file": "<업무 후 파일 이름>", "sheet": "<업무 후 시트 이름>"}}]}}
- 각 단계의 결과는 "as" 이름의 표가 됩니다 ("as"를 생략하면 입력 표를 덮어씀). 표 이름에 ':'가 있으면 그대로 씁니다.

## Word 양식 채우기 (inspect_files의 word_templates가 있을 때)
- 빈 양식(업무 전 .docx)과 직원이 채운 예시(업무 후 .docx)를 비교해 채울 칸(s1, s2…)을 미리 찾아 두었습니다.
  각 칸의 label(칸 이름 짐작), context(주변 글자), examples(예시 문서별 실제 값)를 보고 어떤 열·계산식인지 정하세요.
- 표의 한 행 = 문서 한 장. 발급 대상 표를 블록으로 만든 뒤 outputs에 Word 출력을 씁니다:
  {{"type": "docx", "table": "<발급 대상 표>", "template": "<양식 파일 이름>", "fill": {{"s1": "<계산식>", ...}},
   "file": "<파일 이름 계산식, 예: concat('재직증명서_', 성명, '.docx')>"}}
- 칸 값은 예시와 글자까지 같아야 합니다: 날짜는 datefmt(열, 'YYYY년 MM월 DD일'), 두 자리 숫자는 pad(month(열), 2),
  금액은 numfmt(열), 고정 글자는 '작은따옴표'. 예시 파일 이름 규칙이 보이면 file 계산식도 맞추세요.
- 결과 문서 수가 예시 수와 다르면 comparison.notes에 나옵니다. 예시가 일부만 올라온 것인지 ask_user로 확인하세요.

## PowerPoint 보고서 채우기 (inspect_files의 ppt_templates가 있을 때)
- 빈 양식과 직원이 만든 예시 보고서를 비교해 글자 칸(s1…), 데이터 표(t1…, 머리글만 있는 표), 차트(c1…)를 찾아 두었습니다.
  각각의 examples가 예시 보고서의 실제 값입니다. 보고서는 실행 한 번에 1개입니다.
- 글자 칸과 file 계산식은 1행짜리 요약 표(fill_table)의 열만 쓸 수 있습니다. 그래서 요약 표를 group_aggregate(by: [])로 만들 때
  글자 칸에 필요한 값을 모두 함께 만드세요 (예: 일자 min→시작일, 일자 max→종료일, 노출수 sum→총노출수 …).
  글자 칸 값은 계산 결과 그대로 들어가므로 쉼표는 numfmt, %·원 같은 단위는 concat으로 붙이세요.
  예: 소수 둘째 자리 % → concat(numfmt(round(클릭수 / 노출수 * 100, 2), 2), '%'), 날짜 → datefmt(시작일, 'YYYY.MM.DD')
- 표 칸의 숫자는 자동으로 천 단위 쉼표가 붙습니다. columns 개수는 양식 표의 칸 수와 같아야 합니다.
- 출력 형식:
  {{"type": "pptx", "table": "<요약 표>", "template": "<양식 파일 이름>", "fill_table": "<1행 요약 표>",
   "fill": {{"s1": "<계산식>", ...}}, "tables": {{"t1": {{"table": "<표>", "columns": ["열", ...]}}}},
   "charts": {{"c1": {{"table": "<표>", "category": "<항목 열>", "series": ["<값 열>", ...]}}}},
   "file": "<파일 이름 계산식 (fill_table 기준)>"}}

## 블록
{ops}

## 계산식 (filter_rows의 condition, add_column의 expr)
파이썬 비슷한 식. 열 이름을 그대로 쓰고, 공백·괄호·숫자로 시작하는 열은 `백틱`으로 감쌉니다.
연산: + - * / // %, 비교 == != > >= < <=, and/or/not, in ['a','b']. 문자열은 '작은따옴표'.
함수: {funcs}
"""

TOOLS = [
    {"name": "inspect_files",
     "description": "올린 파일들의 표 구조(열, 형식, 값 범위, 샘플 행)를 업무 전/업무 후/참고 문서로 나눠 보여 준다.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "preview_table",
     "description": "표의 실제 행을 본다. role은 before(업무 전) 또는 after(업무 후).",
     "input_schema": {"type": "object", "properties": {
         "role": {"type": "string", "enum": ["before", "after"]},
         "table": {"type": "string", "description": "inspect_files의 표 이름"},
         "rows": {"type": ["integer", "null"], "description": "볼 행 수 (기본 30, 최대 200)"}},
         "required": ["role", "table", "rows"], "additionalProperties": False}},
    {"name": "test_recipe",
     "description": "레시피를 업무 전 파일에 실행하고, 업무 후 파일(엑셀 표·Word 예시)과 셀·칸 단위로 비교한 결과(일치율, 다른 값 목록)를 돌려준다.",
     "input_schema": {"type": "object", "properties": {
         "recipe_json": {"type": "string", "description": "레시피 JSON 문자열"},
         "param_values_json": {"type": ["string", "null"], "description": "이번 예시에 쓸 실행값 JSON (예: {\"지급월\": 11}). 없으면 null"}},
         "required": ["recipe_json", "param_values_json"], "additionalProperties": False}},
    {"name": "ask_user",
     "description": "모호한 규칙을 사용자에게 묻는다. 답을 받을 때까지 기다린다.",
     "input_schema": {"type": "object", "properties": {
         "question": {"type": "string"},
         "options": {"type": "array", "items": {"type": "string"}, "description": "선택지 2~4개"},
         "why": {"type": "string", "description": "왜 묻는지 한 줄"}},
         "required": ["question", "options", "why"], "additionalProperties": False}},
    {"name": "propose_automation",
     "description": "마지막으로 테스트한 레시피를 사용자에게 저장 승인 요청한다.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "자동화 이름 (예: 거래처별 매출 집계)"},
         "summary": {"type": "string", "description": "무엇을 어떻게 처리하는지 2~3문장"}},
         "required": ["name", "summary"], "additionalProperties": False}},
    {"name": "search_docs",
     "description": "사용자가 올린 참고 문서(업무 매뉴얼·규정)에서 관련 내용을 찾는다.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False}},
]

LABELS = {"inspect_files": "파일 구조 분석", "preview_table": "표 내용 보기", "test_recipe": "레시피 실행·검증",
          "ask_user": "질문", "propose_automation": "저장 승인 요청", "search_docs": "참고 문서 검색"}


class BuildSession:
    kind = "build"

    def __init__(self, prompt: str, uploads: dict[str, bytes], labels: dict[str, str]):
        self.id = uuid.uuid4().hex[:12]
        self.out_dir = OUT_DIR / self.id
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.prompt = prompt
        self.labels = labels
        self.before: dict[str, pd.DataFrame] = {}
        self.after: dict[str, pd.DataFrame] = {}
        self.files = {"before": [], "after": [], "docs": []}
        self.load_errors: list[str] = []
        self.docs = DocIndex()
        self.templates: dict[str, dict] = {}       # Word 양식 (업무 전 .docx) → 빈칸 정보
        self.after_docs: dict[str, bytes] = {}     # 직원이 직접 채운 Word 예시 (업무 후 .docx)
        self.after_ppts: dict[str, bytes] = {}     # 직원이 직접 만든 PPT 보고서 예시 (업무 후 .pptx)
        self._raw = {"before": {}, "after": {}}    # 파일 이름 → 읽은 표 (대화 중 파일을 더해도 다시 묶을 수 있게)
        self._word_before: dict[str, bytes] = {}
        self._ppt_before: dict[str, bytes] = {}
        self._file_errors: list[str] = []
        self._pending_note = ""
        self._add(uploads, labels)
        self.mode = detect_provider()
        self.loop = None
        if self.mode in ("openai", "claude"):
            tools = [t for t in TOOLS if t["name"] != "search_docs" or self.docs.chunks]
            system = SYSTEM.format(today=date.today().isoformat(), ops=ops_help(), funcs=FUNC_HELP)
            self.loop = ToolLoop(self.mode, system, tools)
        self.draft: dict | None = None
        self.draft_params: dict = {}
        self.last_test: dict | None = None
        self.proposal: dict | None = None
        self.saved_id: str | None = None
        self._errors: list[str] = []
        self.lock = threading.Lock()

    # ───── 파일 읽기 (처음 + 대화 중 추가) ─────
    def _add(self, uploads: dict[str, bytes], labels: dict[str, str]) -> list[str]:
        added = []
        for name, data in uploads.items():
            role = "after" if labels.get(name) == "업무 후" else "before"
            ext = Path(name).suffix.lower()
            self.labels[name] = "업무 후" if role == "after" else ("참고 문서" if ext in DOC_EXT else "업무 전")
            try:
                if ext in TABLE_EXT:
                    self._raw[role][name] = tb.read_file(name, data)
                elif ext in PPT_EXT:
                    (self.after_ppts if role == "after" else self._ppt_before)[name] = data
                elif ext in WORD_EXT:
                    (self.after_docs if role == "after" else self._word_before)[name] = data
                elif ext in DOC_EXT:
                    self.docs.add_document(Path(name).stem, data.decode("utf-8", "ignore"))
                    role = "docs"
                else:
                    self._file_errors.append(f"{name}: 지금은 엑셀(.xlsx)·CSV·PDF·Word(.docx)·PowerPoint(.pptx)를 자동화할 수 있습니다.")
                    continue
                for r in ("before", "after", "docs"):          # 같은 이름을 다른 칸으로 다시 올린 경우
                    if name in self.files[r]:
                        self.files[r].remove(name)
                self.files[role].append(name)
                added.append(name)
            except Exception as e:  # 파일을 못 읽으면 AI와 사용자 모두에게 알림
                self._file_errors.append(f"{name}: {e}")
        self._rebuild()
        return added

    def _rebuild(self):
        def merged(role):
            out = {}
            for name in self.files[role]:
                out.update(self._raw[role].get(name, {}))
            return tb.group_pdf(out)     # 같은 양식 PDF 여러 장 → 표 하나 (다음 달 장수가 달라도 같은 레시피)
        self.before, self.after = merged("before"), merged("after")
        errors = list(self._file_errors)
        self.templates = {}
        for name, data in self._word_before.items():
            try:
                t = docgen.prepare_template(name, data, self.after_docs)
                self.templates[name] = t
                errors += [f"{name}: {w}" for w in t["warnings"]]
            except Exception as e:
                errors.append(f"{name}: Word 양식을 읽지 못했습니다 ({e})")
        for name, data in self._ppt_before.items():
            try:
                t = pptgen.prepare_template(name, data, self.after_ppts)
                t["kind"] = "pptx"
                self.templates[name] = t
                errors += [f"{name}: {w}" for w in t["warnings"]]
            except Exception as e:
                errors.append(f"{name}: PPT 양식을 읽지 못했습니다 ({e})")
        if self.after_ppts and not self._ppt_before:
            errors.append("업무 후 PPT 예시가 있지만 빈 양식(업무 전 .pptx)이 없습니다. 빈 양식도 함께 올려 주세요.")
        if self.after_docs and not self._word_before:
            errors.append("업무 후 Word 예시가 있지만 빈 양식(업무 전 .docx)이 없습니다. 빈 양식도 함께 올려 주세요.")
        self.load_errors = errors

    def add_files(self, uploads: dict[str, bytes], labels: dict[str, str]) -> dict:
        """대화 도중 파일 추가 → 다음 메시지에서 AI에게 알리고 다시 확인하게 함"""
        with self.lock:
            before_errors = len(self._file_errors)
            added = self._add(uploads, labels)
            if self.loop and self.docs.chunks and not any(t["name"] == "search_docs" for t in self.loop.tools):
                self.loop.tools = self.loop.tools + [t for t in TOOLS if t["name"] == "search_docs"]
            if added:
                desc = ", ".join(f"{n}({self.labels.get(n)})" for n in added)
                self._pending_note += (f"[사용자가 파일을 추가했습니다] {desc}\n"
                                       "inspect_files로 바뀐 파일 구성을 다시 확인한 뒤, 필요하면 레시피를 고쳐 test_recipe로 다시 검증하세요.\n")
            return {"added": [{"name": n, "label": self.labels.get(n)} for n in added],
                    "errors": self._file_errors[before_errors:]}

    # ───── 도구 실행 ─────
    def _exec(self, cid: str, name: str, args: dict):
        yield {"type": "tool_call", "id": cid, "name": name, "label": LABELS.get(name, name), "input": _short(args)}
        try:
            result = getattr(self, f"_t_{name}")(args)
            ok = "error" not in result
        except Exception as e:
            result, ok = {"error": f"{type(e).__name__}: {e}"}, False
        ev = {"type": "tool_result", "id": cid, "name": name, "ok": ok, "summary": self._summary(name, ok, result)}
        yield ev
        if name == "test_recipe" and self.draft is not None:
            yield {"type": "recipe", "recipe": {k: v for k, v in self.draft.items() if k != "assets"}, "steps": self._steps_view(), "test": self.last_test}
        if name == "propose_automation" and ok:
            yield {"type": "proposal", **self.proposal}
        return ok, result

    def _t_inspect_files(self, _):
        return {
            "before": [tb.profile(k, v) for k, v in self.before.items()],
            "after": [tb.profile(k, v) for k, v in self.after.items()],
            "after_files": self.files["after"],
            "docs": self.files["docs"],
            "load_errors": self.load_errors,
            "word_templates": [{"template": n, "slots": [{k: v for k, v in s.items() if k in ("id", "label", "context", "examples")}
                                                         for s in t["slots"]]} for n, t in self.templates.items() if t.get("kind") != "pptx"],
            "word_examples": list(self.after_docs),
            "ppt_templates": [{"template": n,
                               "text_slots": [{k: v for k, v in s.items() if k in ("id", "label", "context", "examples")} for s in t["slots"]],
                               "tables": [{k: v for k, v in x.items() if k in ("id", "header", "examples")} for x in t["tables"]],
                               "charts": [{k: v for k, v in x.items() if k in ("id", "type", "series", "examples")} for x in t["charts"]]}
                              for n, t in self.templates.items() if t.get("kind") == "pptx"],
            "ppt_examples": list(self.after_ppts),
            "note": "업무 후 파일이 없습니다. 설명대로 레시피를 만들고 결과 미리보기를 사용자에게 확인받으세요."
                    if not (self.after or self.after_docs or self.after_ppts) else "",
        }

    def _t_preview_table(self, a):
        src = self.after if a["role"] == "after" else self.before
        if a["table"] not in src:
            return {"error": f"'{a['table']}' 표가 없습니다. 있는 표: {list(src)}"}
        df = src[a["table"]]
        n = min(int(a.get("rows") or 30), 200)
        return {"table": a["table"], "rows_total": int(len(df)), "columns": list(map(str, df.columns)), "rows": tb.records(df, n)}

    def _t_test_recipe(self, a):
        try:
            recipe = json.loads(a["recipe_json"])
        except json.JSONDecodeError as e:
            return {"error": f"recipe_json이 올바른 JSON이 아닙니다: {e}"}
        params = {}
        if a.get("param_values_json"):
            try:
                params = json.loads(a["param_values_json"]) or {}
            except json.JSONDecodeError:
                return {"error": "param_values_json이 올바른 JSON이 아닙니다."}
        recipe = rc.normalize(recipe)
        used = {o.get("template") for o in recipe.get("outputs") or [] if isinstance(o, dict) and o.get("type") in ("docx", "pptx")}
        recipe["assets"] = {"templates": {n: self.templates[n] for n in used if n in self.templates}} if used else {}
        if used - set(self.templates):
            return {"error": f"Word 양식 {sorted(used - set(self.templates))}이(가) 없습니다. 올린 양식: {list(self.templates)}"}
        if not recipe["assets"]:
            recipe.pop("assets")
        errs = rc.validate(recipe)
        if errs:
            return {"error": " / ".join(errs)}
        missing_inputs = [i["table"] for i in recipe["inputs"] if i["table"] not in self.before]
        if missing_inputs:
            return {"error": f"inputs의 표 {missing_inputs}가 업무 전 파일에 없습니다. 있는 표: {list(self.before)}"}
        self.draft, self.draft_params = recipe, params
        try:
            outputs, log = rc.run(recipe, {i["table"]: self.before[i["table"]] for i in recipe["inputs"]}, params)
        except rc.RecipeError as e:
            self.last_test = {"ok": False, "error": str(e)}
            self._errors.append(str(e))
            hint = "오류 메시지의 열 이름·표 이름을 확인하고 다시 test_recipe 하세요."
            if self._errors[-3:].count(str(e)) >= 2:
                hint = ("같은 오류가 반복됩니다. 각 단계의 결과 표 이름은 params 안의 \"as\"로 지정하고, "
                        "다음 단계는 그 이름을 table로 씁니다. 예: {\"op\": \"filter_rows\", \"params\": "
                        "{\"table\": \"원본\", \"condition\": \"금액 >= 300000\", \"as\": \"고액\"}}")
            return {"error": str(e), "hint": hint}
        self._outputs = outputs
        result = {"ok": True, "steps": [{k: s.get(k) for k in ("step", "op", "output", "rows", "columns")} for s in log],
                  "outputs": {k: rc.to_bytes_preview(v, 8) for k, v in outputs.items()}}
        tables_out = {k: v for k, v in outputs.items() if "docx" not in v.attrs and "pptx" not in v.attrs}
        doc_lists = [v for v in outputs.values() if "docx" in v.attrs]
        reports = [v for v in outputs.values() if "pptx" in v.attrs]
        if self.after or self.after_docs or self.after_ppts:
            parts = []
            if self.after:
                parts.append(cmp.compare(tables_out, self.after))
            if self.after_docs:
                dres = docgen.compare_docs(doc_lists, self.after_docs)
                total = sum(r["cells_total"] for r in dres)
                matched = sum(r["cells_matched"] for r in dres)
                notes = []
                made = sum(len(d) for d in doc_lists)
                if made != len(self.after_docs):
                    notes.append(f"결과 문서 {made}장, 업무 후 예시 {len(self.after_docs)}장 — 예시가 일부만인지 사용자에게 ask_user로 확인하세요")
                parts.append({"overall_match_rate": round(matched / total, 4) if total else 0.0,
                              "perfect": bool(dres) and all(r["match_rate"] == 1.0 for r in dres), "tables": dres, "notes": notes})
            if self.after_ppts:
                pres = pptgen.compare(reports, self.after_ppts)
                parts.append({"overall_match_rate": pres[0]["match_rate"], "perfect": all(r.get("perfect") for r in pres),
                              "tables": pres, "notes": []})
            total = sum(t.get("cells_total", 0) for c in parts for t in c["tables"])
            matched = sum(t.get("cells_matched", 0) for c in parts for t in c["tables"])
            comp = {"overall_match_rate": round(matched / total, 4) if total else 0.0, "perfect": all(c["perfect"] for c in parts),
                    "tables": [t for c in parts for t in c["tables"]], "notes": [n for c in parts for n in c.get("notes", [])]}
            result["comparison"] = comp
            if not comp["perfect"]:
                result["problems"] = [f"[{t.get('expected')}] {p}" for t in comp["tables"] for p in (t.get("problems") or [t.get("note", "")])]
                result["hint"] = ("problems를 하나씩 고치세요. 열 선택·순서는 outputs 항목에 \"columns\": [업무 후 열 순서]를 "
                                  "지정하면 맞출 수 있습니다. Word 칸은 예시 값과 글자까지 같아야 합니다 (날짜는 datefmt, 숫자는 numfmt·pad). "
                                  "데이터로 설명되지 않는 차이만 ask_user로 물어보세요.")
        self.last_test = {"ok": True, "comparison": result.get("comparison"), "outputs": {k: rc.to_bytes_preview(v, 50) for k, v in outputs.items()},
                          "log": log}
        return result

    def _t_ask_user(self, a):   # 실제로는 ToolLoop가 멈추고 질문을 띄움
        return {"answer": ""}

    def _t_propose_automation(self, a):
        if not self.last_test or not self.last_test.get("ok"):
            return {"error": "먼저 test_recipe가 성공해야 합니다."}
        comp = self.last_test.get("comparison")
        if comp and not comp["perfect"]:
            return {"error": f"아직 업무 후 파일과 일치하지 않습니다 (일치율 {comp['overall_match_rate']:.1%}). "
                             "차이를 고치거나 ask_user로 사용자에게 확인하세요."}
        self.proposal = {"name": a["name"], "summary": a["summary"],
                         "match_rate": comp["overall_match_rate"] if comp else None, "verified": bool(comp)}
        return {"ok": True, "message": "사용자에게 저장 승인을 요청했습니다. 사용자의 답을 기다리세요."}

    def _auto_propose(self) -> bool:
        comp = (self.last_test or {}).get("comparison")
        if not (self.last_test and self.last_test.get("ok") and comp and comp["perfect"]):
            return False
        after_tables = [n for n in self.files["after"] if Path(n).suffix.lower() != ".docx"]
        if after_tables:
            stem = Path(after_tables[0]).stem
        elif self.templates:
            first = next(iter(self.templates))
            stem = re.sub(r"[\s_\-]*(빈\s*)?양식$", "", Path(first).stem) + ("" if first.lower().endswith(".pptx") else " 발급")
        else:
            stem = "새 자동화"
        name = re.sub(r"[\s_\-.]*(20\d\d)[-_.]?\d{0,2}$", "", stem).strip(" _-.") or stem
        first = next((ln.strip() for ln in self.prompt.splitlines() if ln.strip()), "")
        self.proposal = {"name": name, "summary": first[:120] or f"{name} 자동화",
                         "match_rate": comp["overall_match_rate"], "verified": True}
        return True

    def _t_search_docs(self, a):
        hits = self.docs.search(a["query"], 4)
        return {"results": hits} if hits else {"results": [], "note": "관련 내용을 찾지 못했습니다."}

    # ───── 화면용 정리 ─────
    def _steps_view(self) -> list[dict]:
        log = {s["step"]: s for s in (self.last_test or {}).get("log", [])}
        out = []
        for i, s in enumerate(self.draft.get("steps", []), start=1):
            op = s.get("op")
            entry = {"step": i, "op": op, "label": OPS.get(op, {}).get("label", op), "note": s.get("note", "")}
            from engine.ops import describe
            entry["say"] = describe(op, s.get("params", {})) if op in OPS else ""
            if i in log:
                entry.update(ok=log[i].get("ok"), rows=log[i].get("rows"), error=log[i].get("error"))
            out.append(entry)
        return out

    def _summary(self, name, ok, r):
        if not ok:
            return r.get("error", "오류")[:200]
        if name == "inspect_files":
            extra = "".join(f" · Word 양식 (칸 {len(t['slots'])}개)" for t in r.get("word_templates", []))
            extra += "".join(f" · PPT 양식 (글자 칸 {len(t['text_slots'])} · 표 {len(t['tables'])} · 차트 {len(t['charts'])})"
                             for t in r.get("ppt_templates", []))
            ex = len(r.get("word_examples", [])) + len(r.get("ppt_examples", []))
            return (f"업무 전 표 {len(r['before'])}개 · 업무 후 표 {len(r['after'])}개" + extra + (f" · 예시 문서 {ex}개" if ex else "")
                    + (f" · 참고 문서 {len(r['docs'])}개" if r["docs"] else ""))
        if name == "preview_table":
            return f"{r['table']} · {r['rows_total']}행 중 {len(r['rows'])}행"
        if name == "test_recipe":
            c = r.get("comparison")
            steps = len(r["steps"])
            if not c:
                return f"{steps}단계 실행 완료 (비교할 업무 후 파일 없음)"
            extra = "" if c["perfect"] else " · " + _diff_hint(c)
            return f"{steps}단계 실행 · 업무 후 파일과 일치율 {c['overall_match_rate']:.1%}{' ✓' if c['perfect'] else ''}{extra}"
        if name == "propose_automation":
            return "저장 승인 요청"
        if name == "search_docs":
            return f"{len(r['results'])}건"
        return "완료"

    # ───── 턴 ─────
    def turn(self, text: str) -> Iterator[dict]:
        with self.lock:
            yield {"type": "mode", "mode": self.mode, "model": model_name(self.mode)}
            if self.loop is None:
                yield {"type": "error", "message": "업무 자동화 만들기는 AI가 필요합니다. 서버에 OPENAI_API_KEY 또는 ANTHROPIC_API_KEY를 설정하세요."}
            else:
                first = not self.loop.messages
                msg = text
                if first:
                    files = ", ".join(f"{n}({self.labels.get(n, '첨부')})" for n in self.labels) or "없음"
                    msg = f"[올린 파일] {files}\n[업무 설명]\n{text}"
                if self._pending_note:
                    msg, self._pending_note = self._pending_note + "\n" + msg, ""
                yield from self.loop.turn(msg, self._exec)
                # AI가 아직 일치하지 않는데 질문도 제안도 없이 멈추면, 최대 3번까지 계속하도록 재촉합니다.
                for _ in range(3):
                    if self.proposal or self.loop.pending or not self.last_test:
                        break
                    comp = self.last_test.get("comparison")
                    if self.last_test.get("ok") and (not comp or comp["perfect"]):
                        nudge = "검증이 끝났으면 propose_automation으로 저장 승인을 요청하세요." if comp else \
                                "결과 미리보기를 사용자에게 ask_user로 확인받은 뒤 propose_automation 하세요."
                    else:
                        nudge = ("[시스템] 아직 업무 후 파일과 완전히 일치하지 않습니다. 마지막 test_recipe의 problems와 diffs를 "
                                 "보고 레시피를 고쳐 다시 test_recipe 하세요. 데이터로 설명되지 않는 차이만 ask_user로 물어보세요.")
                    yield from self.loop.turn(nudge, self._exec)
                # 재촉해도 AI가 저장 요청을 빠뜨리면: 검증이 100%로 끝난 경우에 한해 시스템이 대신 저장 승인을 요청
                if not self.proposal and not self.loop.pending and self._auto_propose():
                    yield {"type": "proposal", **self.proposal}
            yield {"type": "build_state", "proposal": self.proposal, "saved_id": self.saved_id,
                   "waiting_answer": bool(self.loop and self.loop.pending)}
            yield {"type": "done"}


def _short(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k == "recipe_json":
            try:
                out["steps"] = len(json.loads(v).get("steps", []))
            except Exception:
                out["recipe"] = "(JSON 오류)"
        elif isinstance(v, str) and len(v) > 80:
            out[k] = v[:80] + "…"
        else:
            out[k] = v
    return out


def _diff_hint(c: dict) -> str:
    parts = []
    for t in c["tables"]:
        parts.extend(t.get("problems") or ([t["note"]] if t.get("note") else []))
    return " / ".join(parts[:3])[:220]
