"""SQLite store for run history. Logs live as files under the data dir.

Data lives in ~/.local/share/cronicle/ (override with CRONICLE_DATA_DIR):
  cronicle.db            run records
  logs/<job_id>/<run>.log   captured stdout/stderr per run
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  job_name TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  exit_code INTEGER,
  status TEXT NOT NULL DEFAULT 'running',
  log_path TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_job_started ON runs(job_id, started_at DESC);
CREATE TABLE IF NOT EXISTS projects(
  name TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);
"""

FULL_LOG_CAP_BYTES = 1024 * 1024  # cap for untailed reads


def data_dir() -> Path:
    d = Path(os.environ.get("CRONICLE_DATA_DIR", Path.home() / ".local/share/cronicle"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "logs").mkdir(exist_ok=True)
    return d


def _db() -> sqlite3.Connection:
    data_dir()
    con = sqlite3.connect(data_dir() / "cronicle.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectError(Exception):
    pass


class ProjectExists(ProjectError):
    pass


class ProjectNotFound(ProjectError):
    pass


def list_projects() -> list[dict]:
    con = _db()
    try:
        rows = con.execute("SELECT name, created_at FROM projects ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def create_project(name: str) -> dict:
    from .core import validate_project_name

    name = validate_project_name(name)
    con = _db()
    try:
        try:
            con.execute("INSERT INTO projects(name, created_at) VALUES (?, ?)",
                        (name, _now_iso()))
            con.commit()
        except sqlite3.IntegrityError:
            raise ProjectExists(f"project '{name}' already exists")
        row = con.execute("SELECT name, created_at FROM projects WHERE name = ?",
                          (name,)).fetchone()
        return dict(row)
    finally:
        con.close()


def rename_project(old: str, new: str) -> dict:
    from .core import validate_project_name

    new = validate_project_name(new)
    con = _db()
    try:
        row = con.execute("SELECT name FROM projects WHERE name = ?", (old,)).fetchone()
        if row is None:
            raise ProjectNotFound(f"no project '{old}'")
        if old != new:
            try:
                con.execute("UPDATE projects SET name = ? WHERE name = ?", (new, old))
                con.commit()
            except sqlite3.IntegrityError:
                raise ProjectExists(f"project '{new}' already exists")
        row = con.execute("SELECT name, created_at FROM projects WHERE name = ?",
                          (new,)).fetchone()
        return dict(row)
    finally:
        con.close()


def delete_project(name: str) -> None:
    con = _db()
    try:
        cur = con.execute("DELETE FROM projects WHERE name = ?", (name,))
        con.commit()
        if cur.rowcount == 0:
            raise ProjectNotFound(f"no project '{name}'")
    finally:
        con.close()


def record_start(job_id: str, job_name: str = "") -> tuple[str, str]:
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"
    log_path = data_dir() / "logs" / job_id / f"{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch()
    con = _db()
    try:
        con.execute(
            "INSERT INTO runs(id, job_id, job_name, started_at, status, log_path)"
            " VALUES (?, ?, ?, ?, 'running', ?)",
            (run_id, job_id, job_name, _now_iso(), str(log_path)),
        )
        con.commit()
    finally:
        con.close()
    return run_id, str(log_path)


def record_finish(run_id: str, exit_code: int) -> dict | None:
    finished = _now_iso()
    status = "ok" if exit_code == 0 else "failed"
    con = _db()
    try:
        row = con.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()
        duration_ms = None
        if row and row["started_at"]:
            try:
                started = datetime.fromisoformat(row["started_at"])
                duration_ms = int((datetime.fromisoformat(finished) - started).total_seconds() * 1000)
            except ValueError:
                pass
        con.execute(
            "UPDATE runs SET finished_at = ?, exit_code = ?, status = ?, duration_ms = ? WHERE id = ?",
            (finished, exit_code, status, duration_ms, run_id),
        )
        con.commit()
    finally:
        con.close()
    return get_run(run_id)


def list_runs(job_id: str | None = None, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 500))
    con = _db()
    try:
        if job_id:
            rows = con.execute(
                "SELECT * FROM runs WHERE job_id = ? ORDER BY started_at DESC, id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_run(run_id: str) -> dict | None:
    con = _db()
    try:
        row = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def read_log(run_id: str, tail: int | None = None) -> dict:
    """Returns {"text": ..., "truncated": bool}."""
    run = get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    path = run.get("log_path") or ""
    if not path or not os.path.exists(path):
        return {"text": "(log file not found)", "truncated": False}
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    if tail is not None:
        tail = max(1, min(tail, 5000))
        lines = text.splitlines()
        if len(lines) > tail:
            return {"text": "\n".join(lines[-tail:]) + "\n", "truncated": True}
        return {"text": text, "truncated": False}
    if len(raw) > FULL_LOG_CAP_BYTES:
        return {"text": text[-FULL_LOG_CAP_BYTES:], "truncated": True}
    return {"text": text, "truncated": False}
