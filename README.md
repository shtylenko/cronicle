# cronicle

Small web UI + CLI for managing cron jobs on a Linux box, with run history
and per-run logs. The web UI is for humans; the CLI is script- and agent-friendly.

- List, add, update, delete jobs in your user crontab
- See recent executions (ok / failed) with durations and exit codes
- Read captured stdout/stderr per run
- Plain-English scheduling in the web UI ("Every Wednesday at 5pm")
- Projects: group jobs, filter the UI by project (rename propagates, delete is
  blocked while jobs are linked)

## How it works

Jobs saved through cronicle get a marker comment and their command is wrapped
with `cronicle-run`, which records the run in SQLite and captures output to a
log file, then exits with the command's own exit code:

```
# cronicle id=1a2b3c4d name=backup
0 2 * * * /path/to/cronicle-run --job-id 1a2b3c4d -- /opt/backup.sh
```

Pre-existing crontab entries (env vars, comments, other jobs) are preserved
verbatim. Unwrapped jobs are still listed; they gain the wrapper the first
time you update them. The UI always shows the original command.

Data lives in `~/.local/share/cronicle/` (`cronicle.db` + `logs/`).
Override with `CRONICLE_DATA_DIR`. Run history is kept when a job is deleted.

## Requirements

- Python 3.10+
- `crontab` available (the cron daemon for your distro):
  - Arch/Omarchy: `sudo pacman -S cronie && sudo systemctl enable --now cronie`
  - Debian/Ubuntu: `sudo apt install cron`

## Install

```sh
cd cronicle
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Web UI

```sh
./run.sh
```

Then open http://127.0.0.1:8231 in a browser. Binds localhost only, no login.
(`CRONICLE_PORT` overrides the port; `cronicle serve` runs without the launcher.)

## CLI

```sh
cronicle jobs list                    # human table
cronicle jobs list --json             # agent-friendly
cronicle jobs add --schedule "0 2 * * *" --command "/opt/backup.sh" --name backup
cronicle jobs update <id> --schedule "30 2 * * *" --disable
cronicle jobs delete <id> --yes

cronicle runs list --limit 20
cronicle runs list --job <id> --json
cronicle runs show <run-id> --tail 50
cronicle runs log <run-id> --tail 200

cronicle projects list
cronicle projects add trading
cronicle projects rename trading markets
cronicle projects delete old-proj --yes
cronicle jobs list --project trading
cronicle jobs update <id> --project trading   # '' unlinks
```

The CLI works directly on the crontab + SQLite store, so no server is needed
(except `serve`). Schedules accept 5-field form or `@reboot/@daily/...`.

## Development

```sh
source .venv/bin/activate
pytest
CRONICLE_FAKE_CRONTAB=/tmp/fake-tab cronicle jobs list   # dev without touching real crontab
```

## Roadmap

- Next-run preview per job
- System crontabs (`/etc/cron.d`) support
- Optional token auth for non-localhost exposure
