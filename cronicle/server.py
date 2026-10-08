"""FastAPI backend: JSON API plus the static web UI. Binds 127.0.0.1 only."""
from __future__ import annotations

import hashlib
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, nlparse, runner, store

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="cronicle")


class JobIn(BaseModel):
    name: str = ""
    schedule: str
    command: str
    description: str = ""
    project: str = ""
    enabled: bool = True


class JobPatch(BaseModel):
    name: str | None = None
    schedule: str | None = None
    command: str | None = None
    description: str | None = None
    project: str | None = None
    enabled: bool | None = None


class ProjectIn(BaseModel):
    name: str


class ProjectPatch(BaseModel):
    name: str


class ParseIn(BaseModel):
    text: str


def _job_dict(j: core.Job) -> dict:
    return {"id": j.id, "name": j.name, "schedule": j.schedule,
            "schedule_human": nlparse.describe_schedule(j.schedule),
            "command": j.command, "description": j.description, "project": j.project,
            "enabled": j.enabled, "wrapped": j.wrapped, "managed": j.managed}


def _run_dict(r: dict) -> dict:
    return {"id": r["id"], "job_id": r["job_id"], "job_name": r.get("job_name") or "",
            "started_at": r["started_at"], "finished_at": r.get("finished_at"),
            "exit_code": r.get("exit_code"), "status": r["status"],
            "duration_ms": r.get("duration_ms")}


@app.exception_handler(core.JobNotFound)
async def _not_found(_, exc: core.JobNotFound):
    raise HTTPException(status_code=404, detail=str(exc))


@app.exception_handler(core.InvalidJob)
async def _bad_request(_, exc: core.InvalidJob):
    raise HTTPException(status_code=400, detail=str(exc))


@app.exception_handler(core.CronUnavailable)
async def _unavailable(_, exc: core.CronUnavailable):
    raise HTTPException(status_code=503, detail=str(exc))


@app.exception_handler(core.CrontabError)
async def _crontab_error(_, exc: core.CrontabError):
    raise HTTPException(status_code=500, detail=str(exc))


@app.exception_handler(store.ProjectExists)
async def _project_exists(_, exc: store.ProjectError):
    raise HTTPException(status_code=409, detail=str(exc))


@app.exception_handler(store.ProjectNotFound)
async def _project_missing(_, exc: store.ProjectError):
    raise HTTPException(status_code=404, detail=str(exc))


def _require_project(name: str | None) -> None:
    if name and name.strip():
        known = {p["name"] for p in store.list_projects()}
        if name.strip() not in known:
            raise core.InvalidJob(
                f"unknown project '{name.strip()}': create it first "
                "via POST /api/projects.")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "crontab_available": core.crontab_available(),
            "data_dir": str(store.data_dir())}


@app.get("/api/jobs")
def jobs_list(project: str | None = Query(default=None)) -> list[dict]:
    jobs = core.list_jobs()
    if project is not None:
        jobs = [j for j in jobs if j.project == project]
    return [_job_dict(j) for j in jobs]


@app.post("/api/jobs", status_code=201)
def jobs_add(body: JobIn) -> dict:
    _require_project(body.project)
    return _job_dict(core.add_job(body.schedule, body.command, body.name,
                                  body.enabled, body.description, body.project))


@app.get("/api/jobs/{job_id}")
def jobs_get(job_id: str) -> dict:
    return _job_dict(core.get_job(job_id))


@app.put("/api/jobs/{job_id}")
def jobs_update(job_id: str, body: JobPatch) -> dict:
    _require_project(body.project)
    return _job_dict(core.update_job(job_id, body.name, body.schedule, body.command,
                                     body.enabled, body.description, body.project))


@app.delete("/api/jobs/{job_id}", status_code=204)
def jobs_delete(job_id: str) -> None:
    core.delete_job(job_id)


@app.post("/api/jobs/{job_id}/run")
def jobs_run(job_id: str) -> dict:
    """Run a job immediately (synchronously) through the logging wrapper."""
    job = core.get_job(job_id)
    exit_code, run_id = runner.run_job(job.id, job.command)
    run = store.get_run(run_id)
    assert run is not None
    return _run_dict(run)


@app.post("/api/parse-schedule")
def parse_schedule(body: ParseIn) -> dict:
    """Turn plain English ('Every Wednesday at 5pm') into a cron schedule."""
    try:
        return {"schedule": nlparse.parse_natural_schedule(body.text)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/runs")
def runs_list(job_id: str | None = Query(default=None),
              project: str | None = Query(default=None),
              limit: int = Query(default=50, le=500)) -> list[dict]:
    if job_id:
        return [_run_dict(r) for r in store.list_runs(job_id, limit)]
    if project is not None:
        ids = [j.id for j in core.list_jobs() if j.project == project]
        merged: list[dict] = []
        for jid in ids:
            merged.extend(store.list_runs(jid, limit))
        merged.sort(key=lambda r: (r["started_at"] or "", r["id"]), reverse=True)
        return [_run_dict(r) for r in merged[:limit]]
    return [_run_dict(r) for r in store.list_runs(None, limit)]


@app.get("/api/projects")
def projects_list() -> list[dict]:
    try:
        counts: dict[str, int] = {}
        for j in core.list_jobs():
            counts[j.project] = counts.get(j.project, 0) + 1
    except core.CronUnavailable:
        counts = {}
    out = []
    for p in store.list_projects():
        out.append({"name": p["name"], "created_at": p["created_at"],
                    "job_count": counts.get(p["name"], 0)})
    return out


@app.post("/api/projects", status_code=201)
def projects_add(body: ProjectIn) -> dict:
    created = store.create_project(body.name)
    return {"name": created["name"], "created_at": created["created_at"], "job_count": 0}


@app.put("/api/projects/{name}")
def projects_rename(name: str, body: ProjectPatch) -> dict:
    new = core.validate_project_name(body.name)
    known = {p["name"] for p in store.list_projects()}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"no project '{name}'")
    if new != name and new in known:
        raise HTTPException(status_code=409, detail=f"project '{new}' already exists")
    relinked = core.rename_project_refs(name, new)
    renamed = store.rename_project(name, new)
    return {"name": renamed["name"], "created_at": renamed["created_at"],
            "job_count": relinked}


@app.delete("/api/projects/{name}", status_code=204)
def projects_delete(name: str) -> None:
    try:
        linked = [j for j in core.list_jobs() if j.project == name]
    except core.CronUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    if linked:
        labels = ", ".join(j.name or j.id for j in linked[:5])
        raise HTTPException(status_code=409, detail=(
            f"project '{name}' still has {len(linked)} job(s): {labels}. "
            "Unlink them first."))
    store.delete_project(name)


@app.get("/api/runs/{run_id}")
def runs_get(run_id: str) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run with id '{run_id}'")
    return _run_dict(run)


@app.get("/api/runs/{run_id}/log")
def runs_log(run_id: str, tail: int | None = Query(default=None)) -> dict:
    try:
        return store.read_log(run_id, tail)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no run with id '{run_id}'")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _static_url(name: str) -> str:
    digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:8]
    return f"/static/{name}?v={digest}"


@app.get("/")
def index():
    # Content-hashed asset URLs so browsers pick up new CSS/JS on refresh
    # instead of serving stale cached copies.
    html = (STATIC_DIR / "index.html").read_text()
    for asset in ("styles.css", "app.js", "favicon.svg"):
        html = html.replace(f"/static/{asset}", _static_url(asset))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8231)
