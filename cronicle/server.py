"""FastAPI backend: JSON API plus the static web UI. Binds 127.0.0.1 only."""
from __future__ import annotations

import shutil
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import core, runner, store

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="cronicle")


class JobIn(BaseModel):
    name: str = ""
    schedule: str
    command: str
    enabled: bool = True


class JobPatch(BaseModel):
    name: str | None = None
    schedule: str | None = None
    command: str | None = None
    enabled: bool | None = None


def _job_dict(j: core.Job) -> dict:
    return {"id": j.id, "name": j.name, "schedule": j.schedule, "command": j.command,
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


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "crontab_available": core.crontab_available(),
            "data_dir": str(store.data_dir())}


@app.get("/api/jobs")
def jobs_list() -> list[dict]:
    return [_job_dict(j) for j in core.list_jobs()]


@app.post("/api/jobs", status_code=201)
def jobs_add(body: JobIn) -> dict:
    return _job_dict(core.add_job(body.schedule, body.command, body.name, body.enabled))


@app.get("/api/jobs/{job_id}")
def jobs_get(job_id: str) -> dict:
    return _job_dict(core.get_job(job_id))


@app.put("/api/jobs/{job_id}")
def jobs_update(job_id: str, body: JobPatch) -> dict:
    return _job_dict(core.update_job(job_id, body.name, body.schedule, body.command, body.enabled))


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


@app.get("/api/runs")
def runs_list(job_id: str | None = Query(default=None),
              limit: int = Query(default=50, le=500)) -> list[dict]:
    return [_run_dict(r) for r in store.list_runs(job_id, limit)]


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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8231)
