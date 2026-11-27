"""AI 도구 호출 루프 (OpenAI / Claude 공통).

- 모델이 도구를 고르면 executor로 실행하고 결과를 돌려주는 과정을 반복합니다.
- ask_user 도구는 특별 취급: 질문을 화면에 띄우고 이번 턴을 멈춘 뒤,
  사용자의 다음 메시지를 그 질문의 답(도구 결과)으로 넘깁니다.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Generator, Iterator

OPENAI_MODEL = os.getenv("WORKCLONE_OPENAI_MODEL", "gpt-4.1-mini")
CLAUDE_MODEL = os.getenv("WORKCLONE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("WORKCLONE_EFFORT", "medium")
MAX_STEPS = int(os.getenv("WORKCLONE_MAX_STEPS", "30"))
OPENAI_MAX_OUT = int(os.getenv("WORKCLONE_OPENAI_MAX_OUTPUT", "6000"))   # 한 번 응답의 최대 길이 (폭주 방지)

# executor(call_id, name, args) → 이벤트를 yield 하고 마지막에 (ok, result) 를 return
Executor = Callable[[str, str, dict], Generator[dict, None, tuple[bool, dict]]]


def detect_provider() -> str:
    forced = os.getenv("WORKCLONE_MODE", "").lower()
    if forced in ("demo", "claude", "openai"):
        return forced
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "demo"


def model_name(provider: str) -> str | None:
    return {"claude": CLAUDE_MODEL, "openai": OPENAI_MODEL}.get(provider)


def _salvage(msg) -> bool:
    """잘린 응답의 도구 인자가 JSON으로 읽히면(뒤에 공백만 붙은 경우 등) 그대로 씀"""
    if not msg.tool_calls:
        return False
    for c in msg.tool_calls:
        raw = (c.function.arguments or "").rstrip()
        try:
            json.loads(raw)                    # 완성된 JSON 뒤에 공백만 붙은 경우만 (중간에 잘린 글은 살리지 않음)
        except json.JSONDecodeError:
            return False
        c.function.arguments = raw
    return True


class ToolLoop:
    def __init__(self, provider: str, system: str, tools: list[dict], pause_tools: tuple[str, ...] = ("ask_user",)):
        if provider not in ("openai", "claude"):
            raise ValueError("AI 모드가 아닙니다 (API 키 필요).")
        self.provider = provider
        self.system = system
        self.tools = tools
        self.pause_tools = pause_tools
        self.messages: list = []
        self.pending: dict | None = None     # 답을 기다리는 질문 {call_id, deferred_results}

    # ───── 공개 ─────
    def turn(self, text: str, execute: Executor) -> Iterator[dict]:
        if self.pending:
            self._answer_pending(text)
        else:
            self._add_user(text)
        for _ in range(MAX_STEPS):
            try:
                calls, texts, stop = self._call_model()
            except _LLMError as e:
                yield {"type": "error", "message": str(e)}
                return
            for t in texts:
                yield {"type": "assistant", "text": t}
            if stop == "error":
                return
            if not calls:
                return
            results, question = [], None
            for cid, name, args in calls:
                if args is None:
                    results.append((cid, False, {"error": "도구 인자가 올바른 JSON이 아닙니다. 다시 호출하세요."}))
                    continue
                if name in self.pause_tools and question is None:
                    question = (cid, args)
                    continue
                ok, res = yield from execute(cid, name, args)
                results.append((cid, ok, res))
            if question:
                cid, args = question
                self.pending = {"call_id": cid, "deferred": results}
                yield {"type": "question", "id": cid, "question": args.get("question", ""),
                       "options": args.get("options") or [], "why": args.get("why", "")}
                return
            self._add_results(results)
        yield {"type": "error", "message": f"도구 호출이 {MAX_STEPS}회를 넘어 중단했습니다."}

    # ───── 메시지 구성 (공급자별) ─────
    def _add_user(self, text: str):
        if self.provider == "openai" and not self.messages:
            self.messages.append({"role": "system", "content": self.system})
        self.messages.append({"role": "user", "content": text})

    def _answer_pending(self, text: str):
        p, self.pending = self.pending, None
        answer = {"answer": text}
        self._add_results(p["deferred"] + [(p["call_id"], True, answer)])

    def _add_results(self, results: list[tuple[str, bool, dict]]):
        if self.provider == "openai":
            for cid, ok, res in results:
                body = res if ok else {"error": res.get("error"), "is_error": True}
                self.messages.append({"role": "tool", "tool_call_id": cid,
                                      "content": json.dumps(body, ensure_ascii=False, default=str)})
        else:
            # Claude: 한 번의 user 메시지에 모든 tool_result를 함께 담습니다.
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "is_error": not ok,
                 "content": json.dumps(res, ensure_ascii=False, default=str)} for cid, ok, res in results]})

    def _call_model(self):
        return self._openai() if self.provider == "openai" else self._claude()

    def _openai(self):
        import openai

        client = openai.OpenAI()
        tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                   "parameters": t["input_schema"], "strict": True}} for t in self.tools]
        tail = ""
        for attempt in range(3):
            try:
                # 첫 시도는 temperature 0. 폭주(같은 글 반복)는 0에서 그대로 반복되기 쉬워 다시 할 때는 조금 올림
                resp = client.chat.completions.create(model=OPENAI_MODEL, messages=self.messages, tools=tools,
                                                      temperature=0 if attempt == 0 else 0.5, parallel_tool_calls=False,
                                                      max_completion_tokens=OPENAI_MAX_OUT)
            except openai.AuthenticationError as e:
                raise _LLMError("OpenAI API 키가 올바르지 않습니다. OPENAI_API_KEY를 확인하세요.") from e
            except openai.RateLimitError as e:
                raise _LLMError("OpenAI 요청 한도(또는 크레딧)를 초과했습니다. 잠시 후 다시 시도하세요.") from e
            except openai.APIStatusError as e:
                raise _LLMError(f"OpenAI API 오류 ({e.status_code}): {e.message}") from e
            except openai.APIConnectionError as e:
                raise _LLMError("OpenAI API에 연결할 수 없습니다.") from e
            choice = resp.choices[0]
            if choice.finish_reason != "length":
                break
            if _salvage(choice.message):          # 도구 인자 뒤에 공백만 끝없이 붙은 경우: 앞부분은 멀쩡하므로 살림
                choice.finish_reason = "tool_calls"
                break
            m = choice.message
            raw = (m.content or "") + "".join(c.function.arguments or "" for c in (m.tool_calls or []))
            tail = repr(raw[-60:])
            if attempt < 2 and not any(x.get("content", "").startswith("[시스템] 직전 응답이 너무 길어") for x in self.messages[-1:]):
                self.messages.append({"role": "user", "content": (
                    "[시스템] 직전 응답이 너무 길어 잘렸습니다. 같은 내용을 반복하지 말고, recipe_json은 공백·줄바꿈 없이 "
                    "꼭 필요한 단계만 간결하게 다시 작성해 도구를 호출하세요.")})
        msg = choice.message
        entry: dict = {"role": "assistant", "content": msg.content or ""}
        calls = []
        if msg.tool_calls:
            entry["tool_calls"] = [{"id": c.id, "type": "function",
                                    "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                   for c in msg.tool_calls]
            for c in msg.tool_calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                calls.append((c.id, c.function.name, args))
        if getattr(msg, "refusal", None):
            raise _LLMError(f"모델이 요청을 거절했습니다: {msg.refusal}")
        if choice.finish_reason == "length":
            raise _LLMError(f"AI 응답이 세 번 연속 길이 제한에 걸려 중단했습니다. '계속해 줘'라고 보내 다시 시도해 주세요. (끝부분: {tail})")
        self.messages.append(entry)
        texts = [msg.content] if msg.content and msg.content.strip() else []
        return calls, texts, "tool_use" if calls else "end"

    def _claude(self):
        import anthropic

        client = anthropic.Anthropic()
        try:
            resp = client.beta.messages.create(
                model=CLAUDE_MODEL, max_tokens=16000, system=self.system, tools=self.tools,
                messages=self.messages, output_config={"effort": CLAUDE_EFFORT},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise _LLMError("API 키가 올바르지 않습니다. ANTHROPIC_API_KEY를 확인하세요.") from e
        except anthropic.RateLimitError as e:
            raise _LLMError("요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from e
        except anthropic.APIStatusError as e:
            raise _LLMError(f"Claude API 오류 ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise _LLMError("Claude API에 연결할 수 없습니다.") from e
        if resp.stop_reason == "refusal":
            raise _LLMError("모델이 이 요청을 처리하지 않았습니다.")
        self.messages.append({"role": "assistant", "content": resp.content})
        texts = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
        if resp.stop_reason == "max_tokens":
            raise _LLMError("응답이 길이 제한에 걸려 중간에 끊겼습니다.")
        calls = [(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return calls, texts, "tool_use" if calls else "end"


class _LLMError(Exception):
    pass


LLMError = _LLMError


def complete(system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000):
    """도구 없이 한 번 묻고 답 받기 (요약·분류·초안 쓰기용).
    schema(JSON Schema)를 주면 그 모양의 dict로, 없으면 글자로 돌려줍니다. AI 키가 없으면 LLMError."""
    provider = detect_provider()
    if provider == "openai":
        import openai
        kw = {}
        if schema:
            kw["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}
        try:
            resp = openai.OpenAI().chat.completions.create(
                model=OPENAI_MODEL, temperature=0.2, max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}], **kw)
        except openai.OpenAIError as e:
            raise _LLMError(f"OpenAI 오류: {e}") from e
        text = resp.choices[0].message.content or ""
        if resp.choices[0].finish_reason == "length":
            raise _LLMError("응답이 길이 제한에 걸려 끊겼습니다.")
    elif provider == "claude":
        import anthropic
        sys_text = system
        if schema:
            sys_text += ("\n\n다음 JSON Schema에 맞는 JSON 객체 하나만 출력하세요. 다른 글은 쓰지 마세요.\n"
                         + json.dumps(schema, ensure_ascii=False))
        try:
            resp = anthropic.Anthropic().beta.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens, system=sys_text, messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except anthropic.AnthropicError as e:
            raise _LLMError(f"Claude 오류: {e}") from e
        text = "".join(b.text for b in resp.content if b.type == "text")
        if schema and "{" in text:
            text = text[text.index("{"):text.rindex("}") + 1]
    else:
        raise _LLMError("AI 키가 설정되지 않았습니다 (OPENAI_API_KEY 또는 ANTHROPIC_API_KEY).")
    if schema:
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise _LLMError("AI 응답을 JSON으로 읽지 못했습니다.") from e
    return text
