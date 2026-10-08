"""CLI for agents and humans. Works directly on the crontab + SQLite store,
so no server needs to be running (except `serve`)."""
from __future__ import annotations

import json
from typing import Optional

import typer

from . import core, nlparse, runner, store

app = typer.Typer(help="cronicle: manage cron jobs, runs, and logs.")
jobs_app = typer.Typer(help="List, add, update, delete cron jobs.")
runs_app = typer.Typer(help="Show recent executions and logs.")
projects_app = typer.Typer(help="Manage projects jobs link to.")
app.add_typer(jobs_app, name="jobs")
app.add_typer(runs_app, name="runs")
app.add_typer(projects_app, name="projects")


def _fail(exc: Exception) -> None:
    typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _job_dict(j: core.Job) -> dict:
    return {"id": j.id, "name": j.name, "schedule": j.schedule,
            "schedule_human": nlparse.describe_schedule(j.schedule),
            "command": j.command, "description": j.description, "project": j.project,
            "enabled": j.enabled, "wrapped": j.wrapped, "managed": j.managed}


def _require_project(name: str | None) -> None:
    if name and name.strip():
        known = {p["name"] for p in store.list_projects()}
        if name.strip() not in known:
            _fail(core.InvalidJob(
                f"unknown project '{name.strip()}': create it first "
                "via `cronicle projects add`."))


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


@jobs_app.command("list")
def jobs_list(json_: bool = typer.Option(False, "--json", help="Machine-readable output."),
              project: Optional[str] = typer.Option(None, "--project", "--proj",
                                                     help="Only this project ('' = unassigned).")) -> None:
    try:
        jobs = core.list_jobs()
    except core.CrontabError as e:
        _fail(e)
        return
    if project is not None:
        jobs = [j for j in jobs if j.project == project]
    if json_:
        typer.echo(json.dumps([_job_dict(j) for j in jobs], indent=2))
        return
    if not jobs:
        typer.echo("No cron jobs.")
        return
    typer.echo(f"{'ID':<10} {'SCHEDULE':<16} {'ON':<3} {'LOG':<4} {'PROJECT':<12} NAME / COMMAND")
    for j in jobs:
        label = j.name or "(unnamed)"
        typer.echo(f"{j.id:<10} {_short(j.schedule, 16):<16} "
                   f"{'y' if j.enabled else 'n':<3} {'y' if j.wrapped else 'n':<4} "
                   f"{_short(j.project or '-', 12):<12} {label} :: {_short(j.command, 60)}")


@jobs_app.command("add")
def jobs_add(
    schedule: str = typer.Option(..., "--schedule", help="'* * * * *' or @daily etc."),
    command: str = typer.Option(..., "--command"),
    name: str = typer.Option("", "--name"),
    description: str = typer.Option("", "--description", "--desc"),
    project: str = typer.Option("", "--project", "--proj"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    _require_project(project)
    try:
        job = core.add_job(schedule, command, name, enabled, description, project)
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
    description: Optional[str] = typer.Option(None, "--description", "--desc"),
    project: Optional[str] = typer.Option(None, "--project", "--proj",
                                           help="Project name ('' unlinks)."),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
) -> None:
    if schedule is None and command is None and name is None and enabled is None and description is None and project is None:
        typer.secho("error: nothing to update (pass --schedule/--command/--name/--desc/--project/--enabled)",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    _require_project(project)
    try:
        job = core.update_job(job_id, name, schedule, command, enabled, description, project)
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
              project: Optional[str] = typer.Option(None, "--project", "--proj",
                                                     help="Only jobs in this project."),
              limit: int = typer.Option(20, "--limit"),
              json_: bool = typer.Option(False, "--json")) -> None:
    if job:
        runs = store.list_runs(job, limit)
    elif project is not None:
        try:
            ids = [j.id for j in core.list_jobs() if j.project == project]
        except core.CrontabError as e:
            _fail(e)
            return
        runs = []
        for jid in ids:
            runs.extend(store.list_runs(jid, limit))
        runs.sort(key=lambda r: (r["started_at"] or "", r["id"]), reverse=True)
        runs = runs[:limit]
    else:
        runs = store.list_runs(None, limit)
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


@projects_app.command("list")
def projects_list(json_: bool = typer.Option(False, "--json")) -> None:
    projects = store.list_projects()
    if json_:
        typer.echo(json.dumps(projects, indent=2))
        return
    if not projects:
        typer.echo("No projects. Add one with `cronicle projects add <name>`.")
        return
    for p in projects:
        typer.echo(p["name"])


@projects_app.command("add")
def projects_add(name: str = typer.Argument(...)) -> None:
    try:
        created = store.create_project(name)
    except (core.InvalidJob, store.ProjectExists) as e:
        _fail(e)
        return
    typer.echo(json.dumps(created, indent=2))


@projects_app.command("rename")
def projects_rename(old: str = typer.Argument(...), new: str = typer.Argument(...)) -> None:
    try:
        new_name = core.validate_project_name(new)
    except core.InvalidJob as e:
        _fail(e)
        return
    known = {p["name"] for p in store.list_projects()}
    if old not in known:
        _fail(store.ProjectNotFound(f"no project '{old}'"))
        return
    if new_name != old and new_name in known:
        _fail(store.ProjectExists(f"project '{new_name}' already exists"))
        return
    try:
        relinked = core.rename_project_refs(old, new_name)
    except core.CrontabError as e:
        _fail(e)
        return
    renamed = store.rename_project(old, new_name)
    typer.echo(f"Renamed '{old}' -> '{renamed['name']}' ({relinked} job(s) relinked).")


@projects_app.command("delete")
def projects_delete(name: str = typer.Argument(...),
                    yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    try:
        linked = [j for j in core.list_jobs() if j.project == name]
    except core.CrontabError as e:
        _fail(e)
        return
    if linked:
        labels = ", ".join(j.name or j.id for j in linked[:5])
        _fail(core.InvalidJob(f"project '{name}' still has {len(linked)} job(s): "
                              f"{labels}. Unlink them first."))
        return
    if not yes and not typer.confirm(f"Delete project '{name}'?"):
        raise typer.Exit(0)
    try:
        store.delete_project(name)
    except store.ProjectNotFound as e:
        _fail(e)
        return
    typer.echo(f"Deleted project '{name}'.")


@app.command("serve")
def serve(port: int = typer.Option(8231, "--port"),
          host: str = typer.Option("127.0.0.1", "--host")) -> None:
    """Start the web UI + API server."""
    import uvicorn

    from .server import app as fastapi_app
    typer.echo(f"cronicle at http://{host}:{port} (Ctrl+C to stop)")
    uvicorn.run(fastapi_app, host=host, port=port)


if __name__ == "__main__":
    app()
