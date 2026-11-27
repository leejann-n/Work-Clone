"""WorkClone 서버 — 로그인 · 업무 자동화 만들기 · 내 자동화 실행 · 회계 급여 정산 에이전트 API.

실행:  uvicorn app:app --reload --port 8000   (backend 폴더에서)
접속:  http://localhost:8000   (frontend 폴더가 옆에 있으면 화면도 함께 제공)
SUPABASE_URL·SUPABASE_PUBLISHABLE_KEY가 있으면 로그인·DB 저장, 없으면 로컬 모드(로그인 없음·폴더 저장).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import supa
from agent import Session as PayrollSession
from auth import Ctx, current
from builder import BuildSession
from engine import recipe as rc
from engine import tables as tb
from llm import detect_provider, model_name
from stores import store_for

BASE = Path(__file__).parent
FRONTEND = BASE.parent / "frontend"
SAMPLE_CSV = BASE / "data" / "samples" / "11_급여대장_2026-11.csv"
SAMPLES = BASE / "data" / "samples" / "excel"
MAX_UPLOAD = 20 * 1024 * 1024

app = FastAPI(title="WorkClone")
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])

SESSIONS: dict[str, tuple[str, object]] = {}     # 세션 id → (만든 직원 id, 세션)


def _session(sid: str, ctx: Ctx):
    item = SESSIONS.get(sid)
    if not item or item[0] != ctx.uid:
        raise HTTPException(404, "작업 세션을 찾을 수 없습니다 (서버가 재시작되었을 수 있습니다). 처음부터 다시 시작해 주세요.")
    return item[1]


async def _read_uploads(groups: list[tuple[str, list[UploadFile]]]) -> tuple[dict[str, bytes], dict[str, str]]:
    uploads, labels = {}, {}
    for label, files in groups:
        for f in files:
            data = await f.read()
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, f"{f.filename}: 파일이 20MB를 넘습니다.")
            name = Path(f.filename or "file").name
            uploads[name] = data
            labels[name] = label
    return uploads, labels


def _download(name: str, data: bytes) -> Response:
    quoted = urllib.parse.quote(name)
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})


@app.get("/api/health")
def health():
    mode = detect_provider()
    return {"mode": mode, "model": model_name(mode), "auth": supa.enabled()}


@app.get("/api/config")
def config():
    return {"auth": supa.enabled(), "daily_ai_limit": auth.DAILY_AI_LIMIT if supa.enabled() else None}


# ───────────── 로그인 ─────────────
class LoginBody(BaseModel):
    emp_no: str
    password: str


class RefreshBody(BaseModel):
    refresh_token: str


@app.post("/api/auth/login")
def login(body: LoginBody):
    return auth.login(body.emp_no, body.password)


@app.post("/api/auth/refresh")
def refresh(body: RefreshBody):
    return auth.refresh(body.refresh_token)


@app.get("/api/auth/me")
def me(ctx: Ctx = Depends(current)):
    return {"employee": ctx.public(), "usage": auth.usage(ctx), "auth": not ctx.local}


@app.post("/api/auth/logout")
def logout(ctx: Ctx = Depends(current)):
    if not ctx.local:
        supa.sign_out(ctx.token)
    return {"ok": True}


# ───────────── 예시 파일 (로그인 없이 공개) ─────────────
@app.get("/api/samples/payroll")
def sample_payroll():
    return FileResponse(SAMPLE_CSV, filename=SAMPLE_CSV.name, media_type="text/csv")


def _scenarios() -> list[dict]:
    p = SAMPLES / "scenarios.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


@app.get("/api/samples/excel")
def sample_list():
    return [{k: sc[k] for k in ("id", "title", "department", "prompt", "before", "after", "next")} for sc in _scenarios()]


@app.get("/api/samples/excel/{scid}/{name}")
def sample_file(scid: str, name: str):
    sc = next((s for s in _scenarios() if s["id"] == scid), None)
    if not sc or name not in sc["before"] + sc["after"] + sc["next"]:
        raise HTTPException(404, "예시 파일이 없습니다.")
    return FileResponse(SAMPLES / scid / name, filename=name)


# ───────────── 작업 세션 (AI) ─────────────
def _is_payroll_register(uploads: dict[str, bytes]) -> bool:
    for name, data in uploads.items():
        if Path(name).suffix.lower() in (".csv", ".xlsx"):
            try:
                for df in tb.read_file(name, data).values():
                    if {"사번", "기본급"} <= set(map(str, df.columns)):
                        return True
            except Exception:
                continue
    return False


@app.post("/api/sessions")
async def create_session(prompt: str = Form(...), before: list[UploadFile] = File(default=[]),
                         after: list[UploadFile] = File(default=[]), ctx: Ctx = Depends(current)):
    uploads, labels = await _read_uploads([("업무 전", before), ("업무 후", after)])
    # 업무 후 파일이 없고 급여대장(사번·기본급)이면 → 규정 기반 급여 정산 에이전트, 그 외 → 자동화 만들기
    if not after and _is_payroll_register(uploads):
        s, kind = PayrollSession(prompt, uploads, labels), "payroll"
    else:
        s, kind = BuildSession(prompt, uploads, labels), "build"
    SESSIONS[s.id] = (ctx.uid, s)
    return {"session_id": s.id, "kind": kind, "mode": s.mode,
            "files": [{"name": n, "label": labels[n]} for n in uploads], "usage": auth.usage(ctx)}


class Message(BaseModel):
    text: str


@app.post("/api/sessions/{sid}/files")
async def add_session_files(sid: str, before: list[UploadFile] = File(default=[]), after: list[UploadFile] = File(default=[]),
                            ctx: Ctx = Depends(current)):
    """대화 도중 파일 추가 (AI 사용 아님: 다음 메시지에서 AI가 새 파일을 확인)"""
    s = _session(sid, ctx)
    if not isinstance(s, BuildSession):
        raise HTTPException(400, "이 작업에서는 대화 중 파일 추가를 지원하지 않습니다.")
    uploads, labels = await _read_uploads([("업무 전", before), ("업무 후", after)])
    if not uploads:
        raise HTTPException(400, "추가할 파일이 없습니다.")
    return s.add_files(uploads, labels)


@app.post("/api/sessions/{sid}/messages")
def send_message(sid: str, msg: Message, ctx: Ctx = Depends(current)):
    s = _session(sid, ctx)
    # AI를 부르는 요청 1회 = 사용 1회 (하루 한도 초과 시 429)
    used = auth.use_ai(ctx, "payroll" if isinstance(s, PayrollSession) else "build") if s.mode in ("openai", "claude") else None

    def stream():
        if used:
            yield json.dumps({"type": "usage", **used}, ensure_ascii=False) + "\n"
        try:
            for ev in s.turn(msg.text):
                yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"
        except Exception as e:  # 화면에 오류를 보여 주고 스트림을 끝냄
            yield json.dumps({"type": "error", "message": f"서버 오류: {e}"}, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


class SaveBody(BaseModel):
    name: str = ""
    summary: str = ""


@app.post("/api/sessions/{sid}/save")
def save_automation(sid: str, body: SaveBody, ctx: Ctx = Depends(current)):
    """사용자 승인: AI가 제안한(검증된) 레시피를 '내 자동화'로 저장 (같은 부서와 공유)"""
    s = _session(sid, ctx)
    if not isinstance(s, BuildSession):
        raise HTTPException(400, "자동화 만들기 세션이 아닙니다.")
    if not s.proposal or not s.draft:
        raise HTTPException(400, "아직 AI가 저장할 자동화를 제안하지 않았습니다.")
    st = store_for(ctx)
    if s.saved_id:
        return st.get_automation(s.saved_id)
    comp = (s.last_test or {}).get("comparison")
    try:
        item = st.save_automation(
            name=body.name.strip() or s.proposal["name"], summary=body.summary.strip() or s.proposal["summary"],
            recipe=s.draft,
            source={"prompt": s.prompt, "files": s.labels, "match_rate": comp["overall_match_rate"] if comp else None,
                    "verified": bool(comp and comp["perfect"]), "example_params": s.draft_params})
    except supa.SupaError as e:
        raise HTTPException(502, f"저장하지 못했습니다: {e}") from e
    s.saved_id = item["id"]
    return item


class Approval(BaseModel):
    role: str        # 담당자 / 부장 / 대표
    name: str = ""


APPROVAL_ORDER = ["담당자", "부장", "대표"]


@app.post("/api/sessions/{sid}/approve")
def approve(sid: str, body: Approval, ctx: Ctx = Depends(current)):
    """급여규정 제14조 3단계 승인. 이체 파일 도구는 이 값을 보고만 동작합니다 (AI가 바꿀 수 없음)."""
    s = _session(sid, ctx)
    if not isinstance(s, PayrollSession):
        raise HTTPException(400, "급여 정산 세션이 아닙니다.")
    st = s.state
    if st.results is None:
        raise HTTPException(400, "아직 급여 계산 결과가 없습니다.")
    if body.role not in APPROVAL_ORDER:
        raise HTTPException(400, "알 수 없는 승인 단계입니다.")
    idx = APPROVAL_ORDER.index(body.role)
    if any(not st.approvals[r] for r in APPROVAL_ORDER[:idx]):
        raise HTTPException(400, "이전 단계 승인이 먼저 필요합니다.")
    st.approvals[body.role] = body.name or body.role
    return {"approvals": st.approvals, "fully_approved": st.fully_approved}


@app.get("/api/sessions/{sid}/files/{name}")
def session_file(sid: str, name: str, ctx: Ctx = Depends(current)):
    s = _session(sid, ctx)
    path = (s.out_dir / name).resolve()
    if path.parent != s.out_dir.resolve() or not path.is_file():
        raise HTTPException(404, "파일이 없습니다.")
    return _download(name, path.read_bytes())


# ───────────── 내 자동화 (같은 부서 공유) ─────────────
def _view(item: dict) -> dict:
    from engine.ops import OPS, describe
    steps = [{"step": i, "op": s["op"], "label": OPS.get(s["op"], {}).get("label", s["op"]),
              "say": describe(s["op"], s["params"]) if s["op"] in OPS else "", "note": s.get("note", "")}
             for i, s in enumerate(rc.normalize(item["recipe"])["steps"], start=1)]
    return {**item, "steps": steps, "inputs": item["recipe"].get("inputs", []), "params": item["recipe"].get("params", []),
            "outputs": item["recipe"].get("outputs", [])}


@app.get("/api/automations")
def automations(ctx: Ctx = Depends(current)):
    return [_view(a) for a in store_for(ctx).list_automations()]


@app.get("/api/automations/{aid}")
def automation(aid: str, ctx: Ctx = Depends(current)):
    item = store_for(ctx).get_automation(aid)
    if not item:
        raise HTTPException(404, "자동화를 찾을 수 없습니다.")
    return _view(item)


@app.delete("/api/automations/{aid}")
def delete_automation(aid: str, ctx: Ctx = Depends(current)):
    if not store_for(ctx).delete_automation(aid):
        raise HTTPException(404, "삭제할 수 없습니다 (내가 만든 자동화만 삭제할 수 있습니다).")
    return {"ok": True}


def _infer_params(recipe: dict, names: list[str], given: dict) -> dict:
    """파일 이름에서 실행값 추측 (예: '거래내역_2026-12.xlsx' → 지급월 12). 사용자가 넣은 값이 우선."""
    out = dict(given)
    joined = " ".join(names)
    ym = re.search(r"(20\d\d)[-_.]?(\d{2})", joined)
    m_kor = re.search(r"(\d{1,2})\s*월", joined)
    for p in recipe.get("params") or []:
        n = p["name"]
        if out.get(n) not in (None, ""):
            continue
        if "월" in n and (ym or m_kor):
            out[n] = int(ym.group(2)) if ym else int(m_kor.group(1))
        elif ("연도" in n or "년" in n) and ym:
            out[n] = int(ym.group(1))
    return out


def _dated(key: str, input_names: list[str]) -> str:
    """결과 파일 이름의 연월을 이번 입력 파일의 연월로 바꿈 (예: 매출집계_2026-11 → 매출집계_2026-12)"""
    file, sheet = key.split("|")
    ym = re.search(r"(20\d\d)[-_.]?(\d{2})", " ".join(input_names))
    new = re.sub(r"(20\d\d)[-_.](\d{2})", f"{ym.group(1)}-{ym.group(2)}" if ym else "", file)
    new = re.sub(r"_+(\.xlsx)$", r"\1", new.replace("__", "_"))
    sheet = re.sub(r"(20\d\d)[-_.](\d{2})", f"{ym.group(1)}-{ym.group(2)}" if ym else "", sheet).strip("_ ") or "Sheet1"
    return f"{new}|{sheet}"


def _date_doc_names(outputs: dict, input_names: list[str]):
    """PPT·Word 결과 파일 이름에 예시 달(예: 2026-11)이 고정돼 있으면 이번 입력 파일의 연월로 바꿈"""
    ym = re.search(r"(20\d\d)[-_.]?(\d{2})", " ".join(input_names))
    if not ym:
        return
    fix = lambda n: re.sub(r"(20\d\d)([-_.])(\d{2})", lambda m: f"{ym.group(1)}{m.group(2)}{ym.group(2)}", n)  # noqa: E731
    for df in outputs.values():
        if "pptx" in df.attrs:
            df.attrs["pptx"]["file"] = fix(df.attrs["pptx"]["file"])
            df.loc[df["항목"] == "파일명", "값"] = df.attrs["pptx"]["file"]


def _input_tables(uploads: dict[str, bytes]) -> dict:
    tables_in = {}
    for name, data in uploads.items():
        if Path(name).suffix.lower() in (".docx", ".pptx"):   # Word·PPT 양식은 자동화에 저장된 것을 씀
            continue
        tables_in.update(tb.read_file(name, data))
    return tb.group_pdf(tables_in)


def execute_automation(ctx: Ctx, aid: str, uploads: dict[str, bytes], given: dict | None = None, source: str | None = None) -> dict:
    """저장된 자동화를 파일에 실행 (화면의 '실행' 버튼과 업무 메일 첨부파일 실행이 같이 씀)"""
    st = store_for(ctx)
    item = st.get_automation(aid)
    if not item:
        raise HTTPException(404, "자동화를 찾을 수 없습니다.")
    recipe = item["recipe"]
    rid = st.new_run_id()
    record = {"id": rid, "automation_id": aid, "automation": item["name"], "inputs": list(uploads)}
    if source:
        record["source"] = source
    try:
        tables_in = _input_tables(uploads)
        mapped, problems = rc.map_inputs(recipe, tables_in)
        if problems:
            raise rc.RecipeError(" / ".join(problems))
        used = _infer_params(recipe, list(uploads), given or {})
        outputs, log = rc.run(recipe, mapped, used)
        outputs = {_dated(k, list(uploads)): v for k, v in outputs.items()}
        _date_doc_names(outputs, list(uploads))
        checks = rc.quality_checks(recipe, mapped, outputs)
        with tempfile.TemporaryDirectory() as tmp:
            written = rc.write_outputs(outputs, Path(tmp))
            for n in written:
                st.save_run_file(rid, n, (Path(tmp) / n).read_bytes())
    except (rc.RecipeError, ValueError) as e:
        st.add_run({**record, "status": "failed", "error": str(e)})
        raise HTTPException(400, f"실행하지 못했습니다: {e}")
    except supa.SupaError as e:
        raise HTTPException(502, f"결과 파일을 저장하지 못했습니다: {e}") from e
    rows = sum(len(v) for v in outputs.values())
    st.add_run({**record, "status": "success", "outputs": written, "rows": rows, "params": used})
    return {"run_id": rid, "automation": item["name"], "status": "success", "params": used, "checks": checks,
            "log": [{k: s.get(k) for k in ("step", "label", "say", "rows")} for s in log],
            "previews": {k: rc.to_bytes_preview(v, 100) for k, v in outputs.items()},
            "files": [{"name": n, "url": f"/api/runs/{rid}/files/{urllib.parse.quote(n)}"} for n in written]}


def suggest_automations(ctx: Ctx, uploads: dict[str, bytes]) -> dict:
    """파일에 바로 실행할 수 있는 자동화 찾기 (AI 없이: 필요한 열이 모두 있는지로 판단)"""
    try:
        tables_in = _input_tables(uploads)
    except ValueError as e:
        return {"candidates": [], "others": [], "note": str(e)}
    cands, others = [], []
    for a in store_for(ctx).list_automations():
        item = store_for(ctx).get_automation(a["id"]) if "recipe" not in a else a
        recipe = (item or {}).get("recipe") or {}
        if not recipe.get("inputs"):
            continue
        _, problems = rc.map_inputs(recipe, tables_in)
        row = {"id": a["id"], "name": a["name"], "owner_name": a.get("owner_name"), "summary": a.get("summary")}
        (others if problems else cands).append({**row, "problems": problems[:2]} if problems else row)
    return {"candidates": cands, "others": others, "tables": list(tables_in)}


@app.post("/api/automations/{aid}/run")
async def run_automation(aid: str, files: list[UploadFile] = File(...), params: str = Form("{}"), ctx: Ctx = Depends(current)):
    uploads, _ = await _read_uploads([("입력", files)])
    try:
        given = json.loads(params or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "실행값 형식이 올바르지 않습니다.")
    return execute_automation(ctx, aid, uploads, given)


@app.get("/api/runs")
def runs(ctx: Ctx = Depends(current)):
    return store_for(ctx).list_runs()


@app.get("/api/runs/{rid}/files/{name}")
def run_file(rid: str, name: str, ctx: Ctx = Depends(current)):
    data = store_for(ctx).read_run_file(rid, name)
    if data is None:
        raise HTTPException(404, "파일이 없습니다.")
    return _download(name, data)


@app.get("/api/stats")
def stats(ctx: Ctx = Depends(current)):
    return store_for(ctx).stats()


# ───────────── 팀원 기능 (backend/features/*.py 자동 연결) ─────────────
import features  # noqa: E402

features.load(app)


@app.get("/api/features")
def feature_list():
    """연결된 팀원 기능과 상태 (오류가 있으면 이유)"""
    return features.LOADED


# 화면 (마지막에 등록해야 /api 경로를 가리지 않음). 백엔드만 따로 배포하면 frontend 폴더가 없을 수 있음.
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
