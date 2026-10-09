"""Read, parse, and modify the current user's crontab.

Jobs saved through cronicle carry a marker comment above their entry::

    # cronicle id=1a2b3c4d name=backup
    0 2 * * * /path/to/cronicle-run --job-id 1a2b3c4d -- '/opt/backup.sh -v'

Pre-existing entries without a marker are still listed (with a stable
content-derived id) and gain a marker the first time they are updated.
All other content (env vars, comments, blank lines) is preserved verbatim.

Set CRONICLE_FAKE_CRONTAB to a file path to read/write that file instead of
the real crontab (used for tests and development).
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

MARKER_RE = re.compile(r"^#\s*cronicle\s+id=(\S+)(?:\s+name=(.*))?\s*$")
AUX_RE = re.compile(r"^#\s*cronicle:([A-Za-z][\w-]*)\s?(.*)$")
PROJECT_NAME_RE = re.compile(r"^[\w\-. ]+$")
FIELD_RE = re.compile(r"^[\dA-Za-z\*,/\-]+$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
AT_SCHEDULES = {"@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"}
WRAP_RE = re.compile(r"(?:cronicle-run|-m\s+cronicle\.runner)\s+--job-id\s+(\S+)\s+--\s+(.*)$", re.DOTALL)


class CrontabError(Exception):
    pass


class JobNotFound(CrontabError):
    pass


class InvalidJob(CrontabError):
    pass


class CronUnavailable(CrontabError):
    pass


@dataclass
class Job:
    id: str
    name: str
    schedule: str
    command: str  # original command (wrapper stripped for display)
    description: str = ""
    project: str = ""
    extra: list[str] = field(default_factory=list)  # unknown aux lines, preserved verbatim
    enabled: bool = True
    wrapped: bool = False  # command runs through cronicle-run, so runs are logged
    managed: bool = False  # has a cronicle marker comment


@dataclass
class _Seg:
    kind: str  # "other" | "job"
    lines: list[str] = field(default_factory=list)  # raw lines to emit on write
    job: Job | None = None


def fake_crontab_path() -> str | None:
    return os.environ.get("CRONICLE_FAKE_CRONTAB")


def crontab_available() -> bool:
    if fake_crontab_path():
        return True
    return shutil.which("crontab") is not None


def read_crontab_text() -> str:
    fake = fake_crontab_path()
    if fake:
        try:
            with open(fake) as f:
                return f.read()
        except FileNotFoundError:
            return ""
    try:
        p = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except FileNotFoundError:
        raise CronUnavailable(
            "`crontab` not found on this machine. Install cron first "
            "(Arch/Omarchy: `sudo pacman -S cronie && sudo systemctl enable --now cronie`; "
            "Debian/Ubuntu: `sudo apt install cron`)."
        )
    if p.returncode != 0:
        if "no crontab" in (p.stderr or "").lower():
            return ""
        raise CrontabError(f"`crontab -l` failed: {(p.stderr or '').strip()}")
    return p.stdout


def write_crontab_text(text: str) -> None:
    if text and not text.endswith("\n"):
        text += "\n"
    fake = fake_crontab_path()
    if fake:
        with open(fake, "w") as f:
            f.write(text)
        return
    try:
        p = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    except FileNotFoundError:
        raise CronUnavailable("`crontab` not found on this machine (see `cronicle jobs list` for install hints).")
    if p.returncode != 0:
        raise CrontabError(f"`crontab -` failed: {(p.stderr or '').strip()}")


def _looks_like_schedule(fields: list[str]) -> bool:
    """True for 5 cron fields containing at least one digit or star.

    The digit/star rule keeps ordinary comments (e.g. `# run backup every
    day at five`) from being mistaken for commented-out cron entries.
    """
    if len(fields) != 5 or not all(FIELD_RE.match(f) for f in fields):
        return False
    return any(re.search(r"[\d*]", f) for f in fields)


def try_parse_entry(line: str) -> tuple[str, str, bool] | None:
    """Parse one crontab line into (schedule, command, enabled), or None."""
    stripped = line.strip()
    if not stripped:
        return None
    enabled = True
    body = stripped
    if stripped.startswith("#"):
        body = stripped[1:].strip()
        enabled = False
    if not body or (enabled and ENV_RE.match(body)):
        return None
    parts = body.split()
    if not parts:
        return None
    if parts[0] in AT_SCHEDULES and len(parts) >= 2:
        schedule = parts[0]
        command = body.split(None, 1)[1]
        return (schedule, command, enabled)
    if len(parts) < 6:
        return None
    if not _looks_like_schedule(parts[:5]):
        return None
    schedule = " ".join(parts[:5])
    command = body.split(None, 5)[5]
    return (schedule, command, enabled)


def unwrap(command: str) -> tuple[str, bool, str | None]:
    """Strip the cronicle-run wrapper. Returns (command, wrapped, job_id)."""
    m = WRAP_RE.search(command.strip())
    if not m:
        return (command, False, None)
    inner = m.group(2).strip()
    if inner[:1] in ("'", '"'):
        # wrap_command() emits the command as one shlex.quote()d word; reverse it.
        # Anything that is not exactly one shell word passes through untouched,
        # which keeps legacy/hand-written entries readable.
        try:
            toks = shlex.split(inner)
        except ValueError:
            toks = []
        if len(toks) == 1:
            inner = toks[0]
    return (inner, True, m.group(1))


def runner_prefix(job_id: str) -> str:
    exe = shutil.which("cronicle-run")
    if exe:
        return f"{exe} --job-id {job_id} --"
    return f"{sys.executable} -m cronicle.runner --job-id {job_id} --"


def wrap_command(job_id: str, command: str) -> str:
    original, _, _ = unwrap(command)
    # Quote as ONE shell word: cron runs the entry via `sh -c`, and an unquoted
    # `&&`/`;`/`|` would split the line so the tail runs outside the wrapper.
    return f"{runner_prefix(job_id)} {shlex.quote(original)}"


def _stable_id(raw_line: str, seen: dict[str, int]) -> str:
    digest = hashlib.sha256(raw_line.encode()).hexdigest()[:8]
    n = seen.get(digest, 0) + 1
    seen[digest] = n
    return digest if n == 1 else f"{digest}-{n}"


def parse_crontab(text: str) -> list[_Seg]:
    lines = text.splitlines()
    segs: list[_Seg] = []
    seen: dict[str, int] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = MARKER_RE.match(line.strip())
        if marker:
            j = i + 1
            descs: list[str] = []
            aux_lines: list[str] = []
            project = ""
            extra: list[str] = []
            while j < len(lines):
                am = AUX_RE.match(lines[j].strip())
                if not am:
                    break
                aux_lines.append(lines[j])
                key, value = am.group(1), am.group(2).strip()
                if key == "desc":
                    descs.append(value)
                elif key == "project" and not project:
                    project = value
                else:
                    extra.append(lines[j])
                j += 1
            if j < len(lines):
                parsed = try_parse_entry(lines[j])
                if parsed:
                    schedule, command, enabled = parsed
                    original, wrapped, _ = unwrap(command)
                    segs.append(_Seg(kind="job", lines=[line] + aux_lines + [lines[j]], job=Job(
                        id=marker.group(1),
                        name=(marker.group(2) or "").strip(),
                        schedule=schedule,
                        command=original,
                        description="\n".join(descs),
                        project=project,
                        extra=extra,
                        enabled=enabled,
                        wrapped=wrapped,
                        managed=True,
                    )))
                    i = j + 1
                    continue
            # orphan marker (no entry below): preserve as ordinary lines
            segs.append(_Seg(kind="other", lines=[line]))
            i += 1
            continue
        parsed = try_parse_entry(line)
        if parsed:
            schedule, command, enabled = parsed
            original, wrapped, wrap_id = unwrap(command)
            segs.append(_Seg(kind="job", lines=[line], job=Job(
                id=wrap_id or _stable_id(line, seen),
                name="",
                schedule=schedule,
                command=original,
                enabled=enabled,
                wrapped=wrapped,
                managed=False,
            )))
        else:
            segs.append(_Seg(kind="other", lines=[line]))
        i += 1
    return segs


def _segments_to_text(segs: list[_Seg]) -> str:
    out: list[str] = []
    for seg in segs:
        out.extend(seg.lines)
    return "\n".join(out) + ("\n" if out else "")


def _find_job_seg(segs: list[_Seg], job_id: str) -> _Seg:
    for seg in segs:
        if seg.kind == "job" and seg.job and seg.job.id == job_id:
            return seg
    raise JobNotFound(f"no cron job with id '{job_id}'")


def validate_schedule(schedule: str) -> str:
    schedule = " ".join(schedule.split())
    if schedule in AT_SCHEDULES:
        return schedule
    fields = schedule.split(" ")
    if not _looks_like_schedule(fields):
        raise InvalidJob(
            f"invalid schedule '{schedule}': expected 5 fields (minute hour day month weekday) "
            f"or one of {sorted(AT_SCHEDULES)}."
        )
    return schedule


def validate_command(command: str) -> str:
    command = command.strip()
    if not command:
        raise InvalidJob("command must not be empty.")
    if "\n" in command:
        raise InvalidJob("command must be a single line.")
    return command


def validate_project_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise InvalidJob("project name must not be empty.")
    if len(name) > 60 or not PROJECT_NAME_RE.match(name):
        raise InvalidJob(
            f"invalid project name {name!r}: use up to 60 letters, digits, "
            "spaces, dots, dashes, underscores.")
    return name


def list_jobs() -> list[Job]:
    text = read_crontab_text()
    return [seg.job for seg in parse_crontab(text) if seg.kind == "job" and seg.job]


def get_job(job_id: str) -> Job:
    segs = parse_crontab(read_crontab_text())
    seg = _find_job_seg(segs, job_id)
    assert seg.job is not None
    return seg.job


def get_job_display_name(job_id: str) -> str:
    """Best-effort name for run records; never raises."""
    try:
        job = get_job(job_id)
    except CrontabError:
        return ""
    return job.name or job.command[:60]


def _marker_line(job_id: str, name: str) -> str:
    name = " ".join(name.split())
    return f"# cronicle id={job_id} name={name}" if name else f"# cronicle id={job_id}"


def _desc_lines(description: str) -> list[str]:
    return [f"# cronicle:desc {dl.strip()}" for dl in description.split("\n") if dl.strip()]


def _job_lines(job: Job, entry: str) -> list[str]:
    lines = [_marker_line(job.id, job.name)]
    if job.project:
        lines.append(f"# cronicle:project {job.project}")
    lines.extend(_desc_lines(job.description))
    lines.extend(job.extra)
    lines.append(entry)
    return lines


def _entry_line(schedule: str, job_id: str, command: str, enabled: bool) -> str:
    entry = f"{schedule} {wrap_command(job_id, command)}"
    return entry if enabled else f"# {entry}"


def add_job(schedule: str, command: str, name: str = "", enabled: bool = True,
            description: str = "", project: str = "") -> Job:
    schedule = validate_schedule(schedule)
    command = validate_command(command)
    if project:
        project = validate_project_name(project)
    job_id = secrets.token_hex(4)
    job = Job(id=job_id, name=" ".join(name.split()), schedule=schedule,
              command=command, description=description.strip(), project=project,
              enabled=enabled, wrapped=True, managed=True)
    text = read_crontab_text()
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n".join(_job_lines(job, _entry_line(schedule, job_id, command, enabled))) + "\n"
    write_crontab_text(text)
    return job


def update_job(job_id: str, name: str | None = None, schedule: str | None = None,
               command: str | None = None, enabled: bool | None = None,
               description: str | None = None, project: str | None = None) -> Job:
    segs = parse_crontab(read_crontab_text())
    seg = _find_job_seg(segs, job_id)
    assert seg.job is not None
    job = seg.job
    new_name = " ".join(name.split()) if name is not None else job.name
    new_schedule = validate_schedule(schedule) if schedule is not None else job.schedule
    new_command = validate_command(command) if command is not None else job.command
    new_enabled = job.enabled if enabled is None else enabled
    new_desc = job.description if description is None else description.strip()
    new_project = job.project if project is None else (
        validate_project_name(project) if project.strip() else "")
    new_job = Job(id=job.id, name=new_name, schedule=new_schedule, command=new_command,
                  description=new_desc, project=new_project, extra=job.extra,
                  enabled=new_enabled, wrapped=True, managed=True)
    seg.lines = _job_lines(new_job, _entry_line(new_schedule, job.id, new_command, new_enabled))
    seg.job = new_job
    write_crontab_text(_segments_to_text(segs))
    return new_job


def rename_project_refs(old: str, new: str) -> int:
    """Point every job linked to `old` at `new`. Returns jobs updated."""
    new = validate_project_name(new)
    segs = parse_crontab(read_crontab_text())
    count = 0
    for seg in segs:
        if seg.kind != "job" or not seg.job or seg.job.project != old:
            continue
        seg.job.project = new
        entry = seg.lines[-1]
        seg.lines = _job_lines(seg.job, entry)
        count += 1
    if count:
        write_crontab_text(_segments_to_text(segs))
    return count


def delete_job(job_id: str) -> None:
    segs = parse_crontab(read_crontab_text())
    seg = _find_job_seg(segs, job_id)
    segs.remove(seg)
    write_crontab_text(_segments_to_text(segs))
