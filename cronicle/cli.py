"""CLI for agents and humans. Works directly on the crontab + SQLite store,
so no server needs to be running (except `serve`)."""
from __future__ import annotations

import json
from typing import Optional

import typer

from . import core, runner, store

app = typer.Typer(help="cronicle: manage cron jobs, runs, and logs.")
jobs_app = typer.Typer(help="List, add, update, delete cron jobs.")
runs_app = typer.Typer(help="Show recent executions and logs.")
app.add_typer(jobs_app, name="jobs")
app.add_typer(runs_app, name="runs")


def _fail(exc: Exception) -> None:
    typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _job_dict(j: core.Job) -> dict:
    return {"id": j.id, "name": j.name, "schedule": j.schedule, "command": j.command,
            "enabled": j.enabled, "wrapped": j.wrapped, "managed": j.managed}


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


@jobs_app.command("list")
def jobs_list(json_: bool = typer.Option(False, "--json", help="Machine-readable output.")) -> None:
    try:
        jobs = core.list_jobs()
    except core.CrontabError as e:
        _fail(e)
        return
    if json_:
        typer.echo(json.dumps([_job_dict(j) for j in jobs], indent=2))
        return
    if not jobs:
        typer.echo("No cron jobs.")
        return
    typer.echo(f"{'ID':<10} {'SCHEDULE':<16} {'ON':<3} {'LOG':<4} NAME / COMMAND")
    for j in jobs:
        label = j.name or "(unnamed)"
        typer.echo(f"{j.id:<10} {_short(j.schedule, 16):<16} "
                   f"{'y' if j.enabled else 'n':<3} {'y' if j.wrapped else 'n':<4} "
                   f"{label} :: {_short(j.command, 70)}")


@jobs_app.command("add")
def jobs_add(
    schedule: str = typer.Option(..., "--schedule", help="'* * * * *' or @daily etc."),
    command: str = typer.Option(..., "--command"),
    name: str = typer.Option("", "--name"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    try:
        job = core.add_job(schedule, command, name, enabled)
    except core.CrontabError as e:
        _fail(e)
        return
    typer.echo(json.dumps(_job_dict(job), indent=2))


@jobs_app.command("show")
def jobs_show(job_id: str = typer.Argument(...)) -> None:
    try:
        job = core.get_job(job_id)
    except core.CrontabError as e:
        _fail(e)
        return
    typer.echo(json.dumps(_job_dict(job), indent=2))


@jobs_app.command("update")
def jobs_update(
    job_id: str = typer.Argument(...),
    schedule: Optional[str] = typer.Option(None, "--schedule"),
    command: Optional[str] = typer.Option(None, "--command"),
    name: Optional[str] = typer.Option(None, "--name"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
) -> None:
    if schedule is None and command is None and name is None and enabled is None:
        typer.secho("error: nothing to update (pass --schedule/--command/--name/--enabled)",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    try:
        job = core.update_job(job_id, name, schedule, command, enabled)
    except core.CrontabError as e:
        _fail(e)
        return
    typer.echo(json.dumps(_job_dict(job), indent=2))


@jobs_app.command("delete")
def jobs_delete(job_id: str = typer.Argument(...),
                yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation.")) -> None:
    try:
        job = core.get_job(job_id)
    except core.CrontabError as e:
        _fail(e)
        return
    label = job.name or job.command
    if not yes and not typer.confirm(f"Delete job {job.id} ({label})?"):
        raise typer.Exit(0)
    try:
        core.delete_job(job_id)
    except core.CrontabError as e:
        _fail(e)
        return
    typer.echo(f"Deleted {job_id}. Run history is kept.")


@runs_app.command("list")
def runs_list(job: Optional[str] = typer.Option(None, "--job", help="Filter by job id."),
              limit: int = typer.Option(20, "--limit"),
              json_: bool = typer.Option(False, "--json")) -> None:
    runs = store.list_runs(job, limit)
    if json_:
        typer.echo(json.dumps(runs, indent=2))
        return
    if not runs:
        typer.echo("No runs recorded yet.")
        return
    typer.echo(f"{'RUN ID':<22} {'JOB':<10} {'STARTED':<20} {'STATUS':<8} {'EXIT':<5} NAME")
    for r in runs:
        typer.echo(f"{r['id']:<22} {r['job_id']:<10} {(r['started_at'] or ''):<20} "
                   f"{r['status']:<8} {str(r.get('exit_code')):<5} {_short(r.get('job_name') or '', 40)}")


@runs_app.command("show")
def runs_show(run_id: str = typer.Argument(...),
              tail: int = typer.Option(50, "--tail", help="Log tail lines (0 = no log).")) -> None:
    run = store.get_run(run_id)
    if run is None:
        typer.secho(f"error: no run with id '{run_id}'", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(run, indent=2))
    if tail > 0:
        typer.echo("--- log ---")
        typer.echo(store.read_log(run_id, tail)["text"], nl=False)


@runs_app.command("log")
def runs_log(run_id: str = typer.Argument(...),
             tail: Optional[int] = typer.Option(None, "--tail", help="Last N lines only.")) -> None:
    if store.get_run(run_id) is None:
        typer.secho(f"error: no run with id '{run_id}'", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    out = store.read_log(run_id, tail)
    typer.echo(out["text"], nl=False)
    if out["truncated"]:
        typer.secho("\n…(truncated)…", fg=typer.colors.YELLOW, err=True)


@app.command("serve")
def serve(port: int = typer.Option(8130, "--port"),
          host: str = typer.Option("127.0.0.1", "--host")) -> None:
    """Start the web UI + API server."""
    import uvicorn

    from .server import app as fastapi_app
    typer.echo(f"cronicle at http://{host}:{port} (Ctrl+C to stop)")
    uvicorn.run(fastapi_app, host=host, port=port)


if __name__ == "__main__":
    app()
