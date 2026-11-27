"""내 자동화·실행 이력·결과 파일 저장소.

- SupaStore: Supabase DB + Storage (로그인한 직원의 토큰으로 접근 → 같은 부서만 보임)
- LocalStore: 서버 폴더 (로컬 개발용, 로그인 없음)
두 저장소는 같은 메서드와 같은 결과 모양을 돌려주므로 app.py는 어느 쪽인지 신경 쓰지 않습니다.
"""
from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import datetime, timedelta, timezone

import supa
from auth import KST, Ctx
from engine import store as local

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _kst(iso: str | None) -> str | None:
    """Supabase 시각(UTC) → 한국 시각 문자열"""
    if not iso:
        return iso
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return dt.astimezone(KST).replace(tzinfo=None).isoformat(timespec="seconds")


class LocalStore:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    def save_automation(self, name, summary, recipe, source, kind="excel"):
        item = local.save_automation(name, summary, recipe, source, department=self.ctx.dept_name)
        return {**item, "owner_name": "나", "is_mine": True}

    def list_automations(self):
        return [{**a, "owner_name": "나", "is_mine": True} for a in local.list_automations()]

    def get_automation(self, aid):
        a = local.get_automation(aid)
        return {**a, "owner_name": "나", "is_mine": True} if a else None

    def delete_automation(self, aid):
        return local.delete_automation(aid)

    def new_run_id(self):
        rid, _ = local.new_run_dir()
        return rid

    def save_run_file(self, rid, name, data: bytes):
        (local.RUN_DIR / rid / name).write_bytes(data)

    def read_run_file(self, rid, name) -> bytes | None:
        d = local.run_dir(rid)
        p = (d / name) if d else None
        if not p or p.resolve().parent != d.resolve() or not p.is_file():
            return None
        return p.read_bytes()

    def add_run(self, record):
        return local.add_run({**record, "run_by_name": "나"})

    def list_runs(self, limit=200):
        return local.list_runs(limit)

    def stats(self):
        return local.stats()


class SupaStore:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.t = ctx.token

    # 직원 이름 표시용
    def _names(self) -> dict:
        return {e["id"]: e["name"] for e in supa.select("employees", self.t, {"select": "id,name"})}

    def _view(self, row: dict, names: dict) -> dict:
        return {"id": row["id"], "name": row["name"], "summary": row["summary"], "department": self.ctx.dept_name,
                "kind": row["kind"], "created_at": _kst(row["created_at"]), "version": row["version"],
                "recipe": row["recipe"], "source": {**(row.get("source") or {}), "match_rate": row.get("match_rate"),
                                                    "verified": row.get("verified")},
                "owner_name": names.get(row["owner_id"], "?"), "is_mine": row["owner_id"] == self.ctx.uid}

    def save_automation(self, name, summary, recipe, source, kind="excel"):
        row = supa.insert("automations", self.t, {
            "owner_id": self.ctx.uid, "department_id": self.ctx.dept_id, "name": name, "summary": summary, "kind": kind,
            "recipe": recipe, "source": source, "match_rate": source.get("match_rate"), "verified": bool(source.get("verified"))})
        return self._view(row, {self.ctx.uid: self.ctx.emp.get("name")})

    def list_automations(self):
        rows = supa.select("automations", self.t, {"select": "*", "is_active": "eq.true", "order": "created_at.desc"})
        runs = supa.select("automation_runs", self.t, {"select": "automation_id,created_at", "order": "created_at.desc", "limit": "1000"})
        names = self._names()
        out = []
        for r in rows:
            v = self._view(r, names)
            mine = [x for x in runs if x["automation_id"] == r["id"]]
            v["run_count"], v["last_run"] = len(mine), (_kst(mine[0]["created_at"]) if mine else None)
            out.append(v)
        return out

    def get_automation(self, aid):
        if not re.fullmatch(r"[0-9a-f-]{36}", aid):
            return None
        rows = supa.select("automations", self.t, {"select": "*", "id": f"eq.{aid}"})
        return self._view(rows[0], self._names()) if rows else None

    def delete_automation(self, aid):
        if not re.fullmatch(r"[0-9a-f-]{36}", aid):
            return False
        return bool(supa.delete("automations", self.t, {"id": f"eq.{aid}", "owner_id": f"eq.{self.ctx.uid}"}))

    def new_run_id(self):
        return str(uuid.uuid4())

    def _path(self, rid, name):
        # Supabase 저장소는 한글 파일 이름을 받지 않아, 이름을 16진수 코드로 바꿔 저장 (내려받을 때는 원래 이름 사용)
        stem, ext = (name.rsplit(".", 1) + [""])[:2]
        return f"{self.ctx.dept_code}/runs/{rid}/{stem.encode('utf-8').hex()}{'.' + ext if ext else ''}"

    def save_run_file(self, rid, name, data: bytes):
        supa.upload(self.t, self._path(rid, name), data, mimetypes.guess_type(name)[0] or XLSX)

    def read_run_file(self, rid, name) -> bytes | None:
        if not re.fullmatch(r"[0-9a-f-]{36}", rid) or "/" in name or ".." in name:
            return None
        try:
            return supa.download(self.t, self._path(rid, name))
        except supa.SupaError:
            return None

    def add_run(self, record):
        row = supa.insert("automation_runs", self.t, {
            "id": record["id"], "automation_id": record.get("automation_id"), "automation_name": record["automation"],
            "run_by": self.ctx.uid, "department_id": self.ctx.dept_id, "status": record["status"],
            "input_files": record.get("inputs", []), "output_files": record.get("outputs", []),
            "params": record.get("params", {}), "row_count": record.get("rows"), "error": record.get("error")})
        return row

    def list_runs(self, limit=200):
        rows = supa.select("automation_runs", self.t, {"select": "*", "order": "created_at.desc", "limit": str(limit)})
        names = self._names()
        return [{"id": r["id"], "automation_id": r["automation_id"], "automation": r["automation_name"], "status": r["status"],
                 "inputs": r["input_files"], "outputs": r["output_files"], "rows": r["row_count"], "error": r["error"],
                 "params": r["params"], "at": _kst(r["created_at"]), "run_by_name": names.get(r["run_by"], "?")} for r in rows]

    def stats(self):
        runs = self.list_runs(1000)
        ok = sum(1 for r in runs if r["status"] == "success")
        since = (datetime.now(KST) - timedelta(days=30)).date().isoformat()
        by_day: dict[str, int] = {}
        for r in runs:
            if r["at"] and r["at"][:10] >= since:
                by_day[r["at"][:10]] = by_day.get(r["at"][:10], 0) + 1
        autos = supa.select("automations", self.t, {"select": "id", "is_active": "eq.true"})
        return {"automations": len(autos), "runs": len(runs),
                "success_rate": round(ok / len(runs) * 100, 1) if runs else None,
                "by_day": sorted(by_day.items()), "recent": runs[:5]}


def store_for(ctx: Ctx):
    return LocalStore(ctx) if ctx.local else SupaStore(ctx)
