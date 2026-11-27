"""Supabase 연결 (추가 설치 없이 파이썬 기본 urllib만 사용).

- 일반 요청은 '로그인한 직원의 토큰'으로 보냅니다 → DB의 RLS 규칙(같은 부서만)이 그대로 적용됩니다.
- secret key는 계정 생성 스크립트에서만 씁니다. 서버는 publishable key만 있으면 됩니다.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

URL = os.getenv("SUPABASE_URL", "").rstrip("/")
PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
EMAIL_DOMAIN = os.getenv("WORKCLONE_EMAIL_DOMAIN", "workclone.test")
BUCKET = "workfiles"


def enabled() -> bool:
    return bool(URL and PUBLISHABLE_KEY)


class SupaError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _headers(key: str, token: str | None) -> dict:
    h = {"apikey": key}
    if token:
        h["Authorization"] = f"Bearer {token}"
    elif not key.startswith("sb_"):
        h["Authorization"] = f"Bearer {key}"
    return h


def request(method: str, path: str, *, key: str | None = None, token: str | None = None, body=None,
            raw: bytes | None = None, content_type: str | None = None, extra: dict | None = None, timeout: int = 30):
    """Supabase REST 호출. 반환: (상태코드, JSON 또는 bytes)"""
    if not URL:
        raise SupaError(500, "SUPABASE_URL이 설정되지 않았습니다.")
    headers = _headers(key or PUBLISHABLE_KEY, token)
    data = None
    if raw is not None:
        data = raw
        headers["Content-Type"] = content_type or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers.update(extra or {})
    req = urllib.request.Request(URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, (json.loads(payload) if payload and "json" in ctype else payload)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "ignore")
        try:
            j = json.loads(text)
            msg = j.get("msg") or j.get("message") or j.get("error_description") or j.get("error") or text
        except json.JSONDecodeError:
            msg = text
        raise SupaError(e.code, str(msg)[:300]) from None
    except urllib.error.URLError as e:
        raise SupaError(503, f"Supabase에 연결할 수 없습니다: {e.reason}") from None


def q(params: dict) -> str:
    return "?" + urllib.parse.urlencode(params, safe="(),.*:")


# ───────────── 로그인 ─────────────
def sign_in(emp_no: str, password: str) -> dict:
    """사번 + 비밀번호 로그인 → 세션(access_token, refresh_token, user)"""
    email = emp_no if "@" in emp_no else f"{emp_no}@{EMAIL_DOMAIN}"
    _, s = request("POST", "/auth/v1/token?grant_type=password", body={"email": email, "password": password})
    return s


def refresh(refresh_token: str) -> dict:
    _, s = request("POST", "/auth/v1/token?grant_type=refresh_token", body={"refresh_token": refresh_token})
    return s


def get_user(token: str) -> dict:
    _, u = request("GET", "/auth/v1/user", token=token)
    return u


def sign_out(token: str) -> None:
    try:
        request("POST", "/auth/v1/logout", token=token)
    except SupaError:
        pass


# ───────────── DB (PostgREST) ─────────────
def select(table: str, token: str, params: dict) -> list:
    _, rows = request("GET", f"/rest/v1/{table}{q(params)}", token=token)
    return rows


def insert(table: str, token: str, row: dict) -> dict:
    _, rows = request("POST", f"/rest/v1/{table}", token=token, body=row, extra={"Prefer": "return=representation"})
    return rows[0] if isinstance(rows, list) and rows else rows


def update(table: str, token: str, params: dict, patch: dict) -> list:
    _, rows = request("PATCH", f"/rest/v1/{table}{q(params)}", token=token, body=patch, extra={"Prefer": "return=representation"})
    return rows


def rpc(fn: str, token: str, args: dict):
    """DB 함수 부르기 (예: send_mail)"""
    _, out = request("POST", f"/rest/v1/rpc/{fn}", token=token, body=args)
    return out


def delete(table: str, token: str, params: dict) -> list:
    _, rows = request("DELETE", f"/rest/v1/{table}{q(params)}", token=token, extra={"Prefer": "return=representation"})
    return rows


def count(table: str, token: str, params: dict) -> int:
    status, rows = request("GET", f"/rest/v1/{table}{q({**params, 'select': 'id'})}", token=token)
    return len(rows)


# ───────────── 파일 저장소 ─────────────
def upload(token: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    request("POST", f"/storage/v1/object/{BUCKET}/{urllib.parse.quote(path)}", token=token, raw=data,
            content_type=content_type, extra={"x-upsert": "true"})
    return path


def download(token: str, path: str) -> bytes:
    _, data = request("GET", f"/storage/v1/object/authenticated/{BUCKET}/{urllib.parse.quote(path)}", token=token)
    return data if isinstance(data, bytes) else json.dumps(data).encode()
