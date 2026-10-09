import pytest

from cronicle import runner, store


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRONICLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CRONICLE_FAKE_CRONTAB", str(tmp_path / "crontab"))
    return tmp_path


def test_successful_run(env):
    code, run_id = runner.run_job("abc123", "echo hello")
    assert code == 0
    run = store.get_run(run_id)
    assert run["status"] == "ok"
    assert run["exit_code"] == 0
    assert run["job_id"] == "abc123"
    log = store.read_log(run_id)
    assert "hello" in log["text"]
    assert not log["truncated"]


def test_failing_run(env):
    code, run_id = runner.run_job("abc123", "echo oops >&2; exit 3")
    assert code == 3
    run = store.get_run(run_id)
    assert run["status"] == "failed"
    assert run["exit_code"] == 3
    assert "oops" in store.read_log(run_id)["text"]


def test_runs_list_filtering(env):
    runner.run_job("job-a", "echo 1")
    runner.run_job("job-b", "echo 2")
    assert len(store.list_runs()) == 2
    assert len(store.list_runs("job-a")) == 1


def test_log_tail(env):
    code, run_id = runner.run_job("abc123", "printf 'a\\nb\\nc\\n'")
    assert code == 0
    out = store.read_log(run_id, tail=2)
    assert out["truncated"]
    assert out["text"].splitlines()[-2:] == ["b", "c"]


def test_quoting_preserved_through_argv(env, capsys):
    # argv word-splitting must not corrupt the command (shlex.join round-trip)
    assert runner.main(["--job-id", "j1", "--", "echo", "a  b"]) == 0
    runs = store.list_runs("j1")
    assert len(runs) == 1
    assert "a  b" in store.read_log(runs[0]["id"])["text"]


def test_single_argv_command_runs_verbatim(env, capsys):
    # the writer quotes the command as one shell word; re-quoting it here
    # would make the inner `sh -c` exec the whole string as one program (127)
    cmd = "cd /tmp && echo chained-left && echo chained-right"
    assert runner.main(["--job-id", "j2", "--", cmd]) == 0
    runs = store.list_runs("j2")
    assert len(runs) == 1
    text = store.read_log(runs[0]["id"])["text"]
    assert "chained-left" in text and "chained-right" in text
