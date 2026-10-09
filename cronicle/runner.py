"""Wrapper that cron invokes instead of the raw command.

Records the run in SQLite, captures stdout/stderr to a log file, and exits
with the command's own exit code (preserving cron's failure semantics).
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys

from . import core, store


def run_job(job_id: str, command: str) -> tuple[int, str]:
    """Run command via /bin/sh. Returns (exit_code, run_id)."""
    name = core.get_job_display_name(job_id)
    run_id, log_path = store.record_start(job_id, name)
    with open(log_path, "w") as f:
        f.write(f"$ {command}\n")
        f.flush()
        p = subprocess.run(command, shell=True, executable="/bin/sh",
                           stdout=f, stderr=subprocess.STDOUT)
    store.record_finish(run_id, p.returncode)
    return p.returncode, run_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cronicle-run",
                                 description="Run a cron command with logging.")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="Command to run, after a `--` separator.")
    args = ap.parse_args(argv)
    rest = list(args.command)
    if rest and rest[0] == "--":
        rest = rest[1:]  # argparse REMAINDER keeps the separator
    if len(rest) == 1:
        # The writer quotes the command as one shell word: use it verbatim.
        # shlex.join() here would wrap it in quotes again, and the inner
        # `sh -c` would then try to execute the whole string as one program.
        command = rest[0].strip()
    else:
        # shlex.join preserves argument boundaries lost to shell word-splitting
        # between the crontab line and argv (quoting, repeated spaces).
        command = shlex.join(rest).strip()
    if not command:
        print("cronicle-run: empty command", file=sys.stderr)
        return 2
    return run_job(args.job_id, command)[0]


if __name__ == "__main__":
    sys.exit(main())
