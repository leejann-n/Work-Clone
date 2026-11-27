"""회계팀 급여 정산 에이전트.

- openai 모드: OPENAI_API_KEY가 있으면 OpenAI(gpt-4.1-mini) function calling으로 같은 도구를 씁니다.

- claude 모드: Claude가 도구 목록을 보고 스스로 어떤 도구를 부를지 판단합니다 (tool calling).
- demo 모드: API 키가 없을 때 화면 시연용으로, 정해진 순서대로 같은 도구를 실행합니다.
  (도구와 계산 결과는 진짜이고, '판단'만 고정된 순서입니다.)

이벤트는 dict로 yield 되어 화면에 실시간으로 전달됩니다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Iterator

from rag import DocIndex
from tools import accounting as acc
from tools.accounting import PayrollState, ToolError

BASE = Path(__file__).parent
KB_DIR = BASE / "data" / "kb"
OUT_DIR = BASE / "output"

MODEL = os.getenv("WORKCLONE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("WORKCLONE_EFFORT", "medium")
OPENAI_MODEL = os.getenv("WORKCLONE_OPENAI_MODEL", "gpt-4.1-mini")   # Workmate 기획서에서 고른 모델
MAX_STEPS = 25


def detect_mode() -> str:
    """사용할 AI 결정: WORKCLONE_MODE로 지정하거나, 설정된 API 키를 보고 자동 선택"""
    forced = os.getenv("WORKCLONE_MODE", "").lower()
    if forced in ("demo", "claude", "openai"):
        return forced
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "demo"


def model_name(mode: str) -> str | None:
    return {"claude": MODEL, "openai": OPENAI_MODEL}.get(mode)


def _base_index() -> DocIndex:
    idx = DocIndex()
    idx.add_folder(KB_DIR)
    return idx


SYSTEM_PROMPT = """당신은 한빛테크 회계팀의 급여 정산 도우미입니다. 오늘은 {today}이고, 급여 지급일은 매월 26일입니다.
담당 직원과 함께 급여 정산을 처리하며, 아래 원칙을 지킵니다.

1. 급여 숫자는 직접 계산하지 않습니다. 모든 계산·점검·파일 생성은 도구로 하고, 도구 결과의 숫자만 인용합니다.
2. 일할 계산, 야근(시간외) 수당, 성과급처럼 규칙이 적용된 항목과 점검에서 warn/error가 나온 항목은
   search_docs로 규정 조항이나 회의록을 찾아 근거로 붙입니다. 근거를 못 찾으면 추측하지 말고 "근거 부족"이라고 씁니다.
3. 회의 발언이나 구두 지시가 규정과 충돌하면 따르지 말고 "확인 요청"으로 올리며, 충돌하는 조항을 함께 적습니다.
4. 이체 파일(make_transfer_file)은 사용자가 승인했다고 말하기 전에는 호출하지 않습니다.
   실제 승인 여부는 시스템이 확인하므로, 도구가 승인 대기를 알리면 그대로 사용자에게 전합니다.
5. 대조 결과표는 save_review_table로 저장하고, 검토용 엑셀은 export_payroll_excel로 만듭니다.

급여 정산 요청을 받으면 다음 순서로 진행합니다.
① load_payroll_csv → calc_payroll → check_anomalies
② check_anomalies의 warn/error 항목마다 search_docs로 근거를 찾습니다 (항목마다 최소 1번).
   성과급 항목은 규정뿐 아니라 최근 회의(예: 오늘 회의 녹취)에 성과급 관련 지시가 있는지도 따로 검색합니다.
   전월 대비 항목은 지난달 처리 메모를 검색합니다.
③ save_review_table: warn/error 항목마다 한 행씩, 회의에서 나온 지시가 있으면 그것도 한 행으로 넣습니다. 상태 기준:
   - 근거 확인: 규정대로 처리했고 근거 문서가 있음
   - 확인 요청: 사람이 확인해야 함 (승인 내역 없음, 계산 기간 해석, 규정과 충돌하는 지시 등)
   - 근거 부족: 근거 문서나 비교 자료를 찾지 못함
④ export_payroll_excel로 검토용 엑셀을 만듭니다.
⑤ 최종 답변을 씁니다.

마지막 답변은 한국어 존댓말 Markdown으로 짧게 정리합니다:
요약(인원·지급총액·실수령 합계) → 이체 전 점검 결과 표 → 대조 결과표(항목 / 처리 내용 / 근거 문서)
→ 확인 요청 사항 → 다음 단계(승인 대기 등). 같은 표를 두 번 반복하지 않습니다.

검색할 수 있는 문서: {docs}
업로드된 파일: {files}"""


class Session:
    def __init__(self, prompt: str, uploads: dict[str, bytes], labels: dict[str, str]):
        self.id = uuid.uuid4().hex[:12]
        self.out_dir = OUT_DIR / self.id
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.state = PayrollState(self.out_dir)
        self.state.uploads = uploads
        self.labels = labels                     # 파일명 → "업무 전" / "업무 후"
        self.first_prompt = prompt
        self.index = _base_index()
        for name, data in uploads.items():       # 업로드한 문서(.md/.txt)도 검색 대상에 추가
            if name.lower().endswith((".md", ".txt")):
                self.index.add_document(Path(name).stem, data.decode("utf-8", "ignore"))
        self.mode = detect_mode()
        self.messages: list[dict] = []
        self.lock = threading.Lock()
        self.demo_done = False

    # ───── 도구 실행 (두 모드 공통) ─────
    def execute(self, name: str, args: dict) -> dict:
        st = self.state
        if name == "load_payroll_csv":
            return acc.load_payroll_csv(st, args.get("file_name"))
        if name == "calc_payroll":
            return acc.calc_payroll(st, args["pay_month"])
        if name == "check_anomalies":
            return acc.check_anomalies(st, args.get("expected_headcount"))
        if name == "search_docs":
            hits = self.index.search(args["query"], args.get("top_k") or 4)
            return {"query": args["query"], "results": hits,
                    "note": "" if hits else "관련 문서를 찾지 못했습니다. 근거 부족으로 처리하세요."}
        if name == "save_review_table":
            return acc.save_review_table(st, args["rows"])
        if name == "export_payroll_excel":
            return acc.export_payroll_excel(st)
        if name == "make_transfer_file":
            return acc.make_transfer_file(st)
        raise ToolError(f"알 수 없는 도구: {name}")

    def run_tool(self, call_id: str, name: str, args: dict) -> Iterator[dict]:
        yield {"type": "tool_call", "id": call_id, "name": name, "label": acc.TOOL_LABELS.get(name, name), "input": args}
        try:
            result = self.execute(name, args)
            ok = True
        except ToolError as e:
            result, ok = {"error": str(e)}, False
        except Exception as e:  # 예상 못 한 오류도 LLM이 알 수 있게 돌려줌
            result, ok = {"error": f"도구 실행 오류: {e}"}, False
        self._last_result = (ok, result)
        ev = {"type": "tool_result", "id": call_id, "name": name, "ok": ok,
              "summary": summarize(name, ok, result), "data": result}
        if ok and name in ("export_payroll_excel", "make_transfer_file"):
            ev["file_url"] = f"/api/sessions/{self.id}/files/{result['file']}"
        yield ev

    # ───── 한 턴 실행 ─────
    def turn(self, text: str) -> Iterator[dict]:
        with self.lock:
            yield {"type": "mode", "mode": self.mode, "model": model_name(self.mode)}
            if self.mode == "claude":
                yield from self._claude_turn(text)
            elif self.mode == "openai":
                yield from self._openai_turn(text)
            else:
                yield from self._demo_turn(text)
            yield {"type": "approval", "approvals": self.state.approvals,
                   "ready": self.state.results is not None}
            yield {"type": "done"}

    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(
            today=date.today().isoformat(),
            docs=", ".join(sorted({c.title for c in self.index.chunks})),
            files=", ".join(f"{n} ({self.labels.get(n, '첨부')})" for n in self.state.uploads) or "없음",
        )

    # ───────────────────────── OpenAI 모드 (function calling) ─────────────────────────
    def _openai_turn(self, text: str) -> Iterator[dict]:
        import openai

        client = openai.OpenAI()   # OPENAI_API_KEY 환경 변수를 읽습니다
        # 같은 도구 정의를 OpenAI 형식으로 변환 (strict: 인자 형식을 스키마대로 강제)
        tools = [{"type": "function",
                  "function": {"name": t["name"], "description": t["description"],
                               "parameters": t["input_schema"], "strict": True}}
                 for t in acc.TOOL_DEFS]
        if not self.messages:
            self.messages.append({"role": "system", "content": self._system_prompt()})
        self.messages.append({"role": "user", "content": text})

        for _ in range(MAX_STEPS):
            try:
                resp = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=self.messages,
                    tools=tools,
                    temperature=0,     # 같은 입력에는 최대한 같은 판단을
                )
            except openai.AuthenticationError:
                yield {"type": "error", "message": "OpenAI API 키가 올바르지 않습니다. OPENAI_API_KEY를 확인하세요."}
                return
            except openai.RateLimitError:
                yield {"type": "error", "message": "OpenAI 요청 한도(또는 크레딧)를 초과했습니다. 잠시 후 다시 시도하세요."}
                return
            except openai.APIStatusError as e:
                yield {"type": "error", "message": f"OpenAI API 오류 ({e.status_code}): {e.message}"}
                return
            except openai.APIConnectionError:
                yield {"type": "error", "message": "OpenAI API에 연결할 수 없습니다. 네트워크를 확인하세요."}
                return

            choice = resp.choices[0]
            msg = choice.message
            calls = msg.tool_calls or []
            entry: dict = {"role": "assistant", "content": msg.content or ""}
            if calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                       for c in calls]
            self.messages.append(entry)
            if msg.content and msg.content.strip():
                yield {"type": "assistant", "text": msg.content}
            if getattr(msg, "refusal", None):
                yield {"type": "error", "message": f"모델이 요청을 거절했습니다: {msg.refusal}"}
                return
            if choice.finish_reason == "length":
                yield {"type": "error", "message": "응답이 길이 제한에 걸려 중간에 끊겼습니다."}
                return
            if not calls:
                return
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                if args is None:
                    content = json.dumps({"error": "도구 인자가 올바른 JSON이 아닙니다. 다시 호출하세요."}, ensure_ascii=False)
                else:
                    yield from self.run_tool(c.id, c.function.name, args)
                    ok, result = self._last_result
                    content = json.dumps(result if ok else {"error": result["error"], "is_error": True},
                                         ensure_ascii=False, default=str)
                # OpenAI는 도구 결과를 호출마다 role=tool 메시지로 돌려줍니다.
                self.messages.append({"role": "tool", "tool_call_id": c.id, "content": content})
        yield {"type": "error", "message": f"도구 호출이 {MAX_STEPS}회를 넘어 중단했습니다."}

    # ───────────────────────── Claude 모드 ─────────────────────────
    def _claude_turn(self, text: str) -> Iterator[dict]:
        import anthropic

        client = anthropic.Anthropic()
        system = self._system_prompt()
        self.messages.append({"role": "user", "content": text})
        for _ in range(MAX_STEPS):
            try:
                resp = client.beta.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    system=system,
                    tools=acc.TOOL_DEFS,
                    messages=self.messages,
                    output_config={"effort": EFFORT},
                    # 안전 분류기가 요청을 거절하면 서버가 자동으로 다른 모델로 다시 시도합니다.
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.AuthenticationError:
                yield {"type": "error", "message": "API 키가 올바르지 않습니다. ANTHROPIC_API_KEY를 확인하세요."}
                return
            except anthropic.RateLimitError:
                yield {"type": "error", "message": "요청 한도를 초과했습니다. 잠시 후 다시 시도하세요."}
                return
            except anthropic.APIStatusError as e:
                yield {"type": "error", "message": f"Claude API 오류 ({e.status_code}): {e.message}"}
                return
            except anthropic.APIConnectionError:
                yield {"type": "error", "message": "Claude API에 연결할 수 없습니다. 네트워크를 확인하세요."}
                return

            if resp.stop_reason == "refusal":
                yield {"type": "error", "message": "모델이 이 요청을 처리하지 않았습니다."}
                return
            # 응답 내용(thinking 블록 포함)을 바꾸지 않고 그대로 대화 기록에 붙입니다.
            self.messages.append({"role": "assistant", "content": resp.content})
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    yield {"type": "assistant", "text": b.text}
            if resp.stop_reason == "pause_turn":
                continue
            if resp.stop_reason == "max_tokens":
                yield {"type": "error", "message": "응답이 길이 제한에 걸려 중간에 끊겼습니다."}
                return

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                return
            results = []
            for tu in tool_uses:
                yield from self.run_tool(tu.id, tu.name, dict(tu.input))
                ok, result = self._last_result
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": json.dumps(result, ensure_ascii=False, default=str),
                                "is_error": not ok})
            # 여러 도구 결과는 한 번의 user 메시지로 함께 돌려줍니다.
            self.messages.append({"role": "user", "content": results})
        yield {"type": "error", "message": f"도구 호출이 {MAX_STEPS}회를 넘어 중단했습니다."}

    # ───────────────────────── 데모 모드 ─────────────────────────
    def _demo_call(self, name: str, args: dict) -> Iterator[dict]:
        yield from self.run_tool(f"demo_{uuid.uuid4().hex[:8]}", name, args)

    def _demo_turn(self, text: str) -> Iterator[dict]:
        st = self.state
        if "승인" in text and st.results is not None:
            yield {"type": "assistant", "text": "승인 상태를 확인하고 이체 파일 생성을 시도하겠습니다."}
            yield from self._demo_call("make_transfer_file", {})
            ok, r = self._last_result
            if ok:
                match = "일치" if r["total_match"] else "불일치"
                yield {"type": "assistant", "text":
                       f"이체 파일을 만들었습니다. **{r['count']}명, 이체 총액 {r['transfer_total']:,}원** "
                       f"(계산값과 {match}).\n\n은행 전송은 담당자가 직접 진행해 주세요."}
            else:
                yield {"type": "assistant", "text": f"이체 파일을 만들지 않았습니다. {r['error']}"}
            return

        if self.demo_done:
            # 데모 모드의 후속 질문: 질문으로 문서를 검색해 근거만 보여 줍니다.
            yield from self._demo_call("search_docs", {"query": text, "top_k": 3})
            ok, r = self._last_result
            lines = [f"- **{h['document']}** · {h['section']} {('(' + h['date'] + ')') if h['date'] else ''}"
                     for h in r.get("results", [])]
            body = "\n".join(lines) if lines else "관련 근거를 찾지 못했습니다 (근거 부족)."
            yield {"type": "assistant", "text":
                   f"질문과 관련된 문서를 찾았습니다.\n\n{body}\n\n"
                   "_데모 모드에서는 검색 결과만 보여 드립니다. API 키를 설정하면 Claude가 직접 판단해 답합니다._"}
            return

        m = re.search(r"(\d{1,2})\s*월", text)
        month = int(m.group(1)) if m else date.today().month
        pay_month = f"{date.today().year}-{month:02d}"

        yield {"type": "assistant", "text": f"{month}월 급여 정산을 시작하겠습니다. 계산은 모두 도구로 처리하고, 규칙이 적용된 항목은 근거를 찾아 표로 정리하겠습니다."}
        yield from self._demo_call("load_payroll_csv", {"file_name": None})
        if not self._last_result[0]:
            yield {"type": "assistant", "text": self._last_result[1]["error"]}
            return
        yield from self._demo_call("calc_payroll", {"pay_month": pay_month})
        yield from self._demo_call("check_anomalies", {"expected_headcount": None})
        checks = self._last_result[1]["checks"]
        warns = {c["항목"]: c for c in checks if c["결과"] != "ok"}

        yield {"type": "assistant", "text": f"확인이 필요한 항목이 {len(warns)}개 있습니다. 규정과 회의록에서 근거를 찾겠습니다."}

        def search(query: str) -> Iterator[dict]:
            yield from self._demo_call("search_docs", {"query": query, "top_k": 3})

        def hits() -> list[dict]:
            return self._last_result[1]["results"]

        def cite(found: list[dict], prefer: tuple[str, ...] = ()) -> str:
            # 선호 문서(규정 → 회의록 순)에서 하나씩 고르고, 같은 문서는 한 번만 인용
            picked: list[dict] = []
            for p in prefer:
                h = next((h for h in found if p in h["file"]), None)
                if h and h not in picked:
                    picked.append(h)
            for h in found:
                if len(picked) >= 2:
                    break
                if all(h["file"] != x["file"] for x in picked):
                    picked.append(h)
            return "; ".join(
                f"{h['document']} {h['section'].split(' > ')[-1]}" + (f" ({h['date']})" if h["date"] and "회의" in h["document"] else "")
                for h in picked[:2]) or "근거 부족"

        rows: list[dict] = []
        res = {x["사번"]: x for x in st.results}

        if "월중 입사자" in warns:
            yield from search("신규 입사자 일할 계산 역일수 식대")
            found = hits()
            for s in warns["월중 입사자"]["대상"]:
                h = res[s]
                rows.append({"item": f"신규 입사자 일할 계산 · {h['이름']}",
                             "treatment": f"기본급·직책수당 {h['근무일수']}/{h['역일수']}일 일할, 식대 전액 지급",
                             "evidence": cite(found, ("01_", "05_")), "status": "근거 확인" if found else "근거 부족"})
        if "계산 기간 확인" in warns:
            yield from search("급여 계산 기간 전월 1일 말일 근태 마감")
            found = hits()
            rows.append({"item": "입사자 포함 시점 (계산 기간)",
                         "treatment": f"규정상 {month}월 지급분 계산 기간은 전월 → {month}월 입사자를 이번 달에 포함할지 담당자 확인 필요",
                         "evidence": cite(found, ("01_", "07_")), "status": "확인 요청" if found else "근거 부족"})
        if "시간외(야근) 수당" in warns:
            yield from search("야근 연장 야간 중복 2.0배 사전 승인")
            found = hits()
            names = ", ".join(res[s]["이름"] for s in warns["시간외(야근) 수당"]["대상"])
            rows.append({"item": f"야근(연장·야간 중복) 수당 · {names}",
                         "treatment": "통상시급 × 2.0배 적용. 사전 승인 내역은 급여대장에 없어 팀장 제출 자료 확인 필요",
                         "evidence": cite(found, ("02_", "04_")), "status": "확인 요청" if found else "근거 부족"})
        if "성과급" in warns:
            yield from search("성과급 지급월 분기 말월")
            found = hits()
            rows.append({"item": "성과급", "treatment": f"{month}월은 분기 말월이 아니므로 전원 0원",
                         "evidence": cite(found, ("01_", "03_")), "status": "근거 확인" if found else "근거 부족"})
            yield from self._demo_call("search_docs", {"query": "오늘 긴급 회의 녹취 성과급 120% 입금", "top_k": 5})
            meeting = [h for h in hits() if "08_" in h["file"]]
            if meeting:
                yield from search("성과급 기준 변경 인사위원회 심의 대표이사 결재 다음 분기")
                rule = [h for h in hits() if "01_" in h["file"]]
                evidence = f"오늘({meeting[0]['date']}) 긴급 회의 녹취"
                if rule:
                    evidence += f"; {rule[0]['document']} {rule[0]['section'].split(' > ')[-1]}"
                rows.append({"item": "오늘 회의의 '성과급 120% 즉시 지급' 지시",
                             "treatment": "급여에 반영하지 않음. 인사위원회 심의·대표 결재 전이며 규정상 즉시 적용 불가 → 부장 확인 요청",
                             "evidence": evidence, "status": "확인 요청"})
        if "전월 대비 총액" in warns:
            yield from search("10월 급여 처리 메모 인원 퇴사자")
            found = [h for h in hits() if "07_" in h["file"]]
            rows.append({"item": "전월 대비 총액 변동",
                         "treatment": "10월 메모로 인원 구성(퇴사 1명 포함)은 확인했으나 10월 지급 총액 수치가 없어 금액 비교 불가",
                         "evidence": (cite(found) + " — 총액 수치 없음") if found else "근거 부족",
                         "status": "근거 부족"})

        yield from self._demo_call("save_review_table", {"rows": rows})
        yield from self._demo_call("export_payroll_excel", {})
        self.demo_done = True

        t = st.totals
        icon = {"ok": "✔", "warn": "⚠", "error": "✖"}
        check_md = "\n".join(f"| {icon[c['결과']]} {c['항목']} | {c['내용']} |" for c in checks)
        review_md = "\n".join(f"| {r['item']} | {r['treatment']} | {r['evidence']} |" for r in rows)
        ask = [r for r in rows if r["status"] != "근거 확인"]
        ask_md = "\n".join(f"{i}. **{r['item']}** — {r['treatment']}" for i, r in enumerate(ask, 1)) or "없음"
        yield {"type": "assistant", "text": f"""### {month}월 급여 정산 결과 (검토용)

**{t['인원']}명 · 지급총액 {t['지급총액']:,}원 · 공제 {t['공제합계']:,}원 · 실수령 합계 {t['실수령액']:,}원**

#### 이체 전 점검
| 항목 | 결과 |
|---|---|
{check_md}

#### 대조 결과표
| 항목 | 처리 내용 | 근거 문서 |
|---|---|---|
{review_md}

#### 확인 요청 사항
{ask_md}

검토용 엑셀을 만들었습니다. **이체 파일은 만들지 않았고, 3단계 승인(담당자 → 부장 → 대표)이 끝날 때까지 대기합니다.**"""}


def summarize(name: str, ok: bool, r: dict) -> str:
    if not ok:
        return r.get("error", "오류")
    if name == "load_payroll_csv":
        return f"{r['file']} · {r['row_count']}명"
    if name == "calc_payroll":
        t = r["totals"]
        return f"{t['인원']}명 · 지급총액 {t['지급총액']:,}원 · 실수령 {t['실수령액']:,}원"
    if name == "check_anomalies":
        s = r["summary"]
        return f"정상 {s['ok']} · 확인 필요 {s['warn']} · 오류 {s['error']}"
    if name == "search_docs":
        hits = r["results"]
        return (f"{len(hits)}건 · " + ", ".join(h["document"] for h in hits[:2])) if hits else "근거를 찾지 못함"
    if name == "save_review_table":
        return f"{r['saved']}개 항목 저장"
    if name in ("export_payroll_excel", "make_transfer_file"):
        return r["file"]
    return "완료"
