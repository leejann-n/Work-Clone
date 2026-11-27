"""로그인·직원 확인 (Supabase Auth).

- 화면은 사번+비밀번호로 로그인하고, 서버가 받은 토큰을 요청마다 Authorization 헤더로 보냅니다.
- 서버는 토큰으로 직원을 확인하고, 그 토큰 그대로 DB를 조회합니다 → DB 권한 규칙(같은 부서만)이 적용됩니다.
- SUPABASE_URL이 없으면 '로컬 모드'로 로그인 없이 동작합니다 (개발·시연용).
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException

import supa

DAILY_AI_LIMIT = int(os.getenv("WORKCLONE_DAILY_AI_LIMIT", "50"))
KST = timezone(timedelta(hours=9))


@dataclass
class Ctx:
    local: bool
    token: str = ""
    uid: str = "local"
    emp: dict = field(default_factory=dict)
    dept_id: int | None = None
    dept_code: str = "LOCAL"
    dept_name: str = "로컬"

    def public(self) -> dict:
        e = self.emp
        return {"id": self.uid, "emp_no": e.get("emp_no"), "name": e.get("name", "로컬 사용자"), "position": e.get("position", ""),
                "department": self.dept_name, "department_code": self.dept_code, "email": e.get("email"),
                "hire_date": e.get("hire_date"), "role": e.get("role", "member")}


_cache: dict[str, tuple[float, Ctx]] = {}
_lock = threading.Lock()


def _claims(token: str) -> dict:
    """토큰(JWT)에서 직원 id·만료 시각 읽기. 진짜 토큰인지는 아래 DB 조회에서 Supabase가 서명을 검사합니다."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _load_ctx(token: str) -> Ctx:
    claims = _claims(token)
    uid = claims.get("sub")
    if not uid or claims.get("exp", 0) < time.time():
        raise HTTPException(401, "로그인이 만료되었습니다. 다시 로그인해 주세요.")
    try:
        rows = supa.select("employees", token, {
            "select": "id,emp_no,name,position,email,hire_date,resign_date,status,role,department_id,departments(code,name)",
            "id": f"eq.{uid}"})
    except supa.SupaError as e:      # 위조·만료 토큰은 Supabase가 거부 (401)
        raise HTTPException(401, "로그인이 만료되었습니다. 다시 로그인해 주세요.") from e
    user = {"id": uid}
    if not rows:
        raise HTTPException(403, "직원 정보가 없는 계정입니다. 관리자에게 문의하세요.")
    emp = rows[0]
    if emp["status"] == "퇴사":
        raise HTTPException(403, "퇴사 처리된 계정은 사용할 수 없습니다.")
    d = emp.get("departments") or {}
    return Ctx(local=False, token=token, uid=user["id"], emp=emp, dept_id=emp["department_id"],
               dept_code=d.get("code", ""), dept_name=d.get("name", ""))


def current(authorization: str | None = Header(default=None)) -> Ctx:
    """모든 API에서 쓰는 '지금 로그인한 직원' 확인"""
    if not supa.enabled():
        return Ctx(local=True)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "로그인이 필요합니다.")
    token = authorization.split(" ", 1)[1].strip()
    now = time.time()
    with _lock:
        hit = _cache.get(token)
        if hit and hit[0] > now:
            return hit[1]
    ctx = _load_ctx(token)
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[token] = (now + 60, ctx)       # 1분 동안 재확인 생략
    return ctx


def login(emp_no: str, password: str) -> dict:
    if not supa.enabled():
        raise HTTPException(400, "로그인 기능이 꺼져 있습니다 (SUPABASE_URL 미설정).")
    emp_no = emp_no.strip()
    if not emp_no or not password:
        raise HTTPException(400, "사번과 비밀번호를 입력해 주세요.")
    try:
        s = supa.sign_in(emp_no, password)
    except supa.SupaError as e:
        if e.status in (400, 401):
            raise HTTPException(401, "사번 또는 비밀번호가 올바르지 않습니다.") from e
        raise HTTPException(502, f"로그인 서버 오류: {e}") from e
    ctx = _load_ctx(s["access_token"])
    return {"access_token": s["access_token"], "refresh_token": s["refresh_token"],
            "expires_at": s.get("expires_at") or int(time.time()) + int(s.get("expires_in", 3600)),
            "employee": ctx.public(), "usage": usage(ctx)}


def refresh(refresh_token: str) -> dict:
    try:
        s = supa.refresh(refresh_token)
    except supa.SupaError as e:
        raise HTTPException(401, "로그인이 만료되었습니다. 다시 로그인해 주세요.") from e
    return {"access_token": s["access_token"], "refresh_token": s["refresh_token"],
            "expires_at": s.get("expires_at") or int(time.time()) + int(s.get("expires_in", 3600))}


# ───────────── AI 사용 횟수 제한 (1인당 하루) ─────────────
def _today_start_utc() -> str:
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def usage(ctx: Ctx) -> dict:
    if ctx.local:
        return {"used": 0, "limit": None}
    rows = supa.select("ai_usage", ctx.token, {"select": "id", "employee_id": f"eq.{ctx.uid}",
                                               "created_at": f"gte.{_today_start_utc()}"})
    return {"used": len(rows), "limit": DAILY_AI_LIMIT}


def use_ai(ctx: Ctx, kind: str) -> dict:
    """AI 요청 1회 기록. 오늘 한도를 넘으면 429."""
    if ctx.local:
        return {"used": 0, "limit": None}
    u = usage(ctx)
    if u["used"] >= DAILY_AI_LIMIT:
        raise HTTPException(429, f"오늘 AI 사용 한도({DAILY_AI_LIMIT}회)를 모두 썼습니다. 내일 다시 이용해 주세요. "
                                 "저장된 자동화 실행은 AI를 쓰지 않아 계속 가능합니다.")
    supa.insert("ai_usage", ctx.token, {"employee_id": ctx.uid, "kind": kind})
    return {"used": u["used"] + 1, "limit": DAILY_AI_LIMIT}
