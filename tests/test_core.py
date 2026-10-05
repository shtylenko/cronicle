import pytest

from cronicle import core


@pytest.fixture
def fake_crontab(tmp_path, monkeypatch):
    p = tmp_path / "crontab"
    p.write_text("")
    monkeypatch.setenv("CRONICLE_FAKE_CRONTAB", str(p))
    monkeypatch.setenv("CRONICLE_DATA_DIR", str(tmp_path / "data"))
    return p


def test_add_and_list(fake_crontab):
    job = core.add_job("0 2 * * *", "/opt/backup.sh", name="backup")
    assert job.wrapped and job.managed and job.enabled
    jobs = core.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].command == "/opt/backup.sh"
    assert jobs[0].name == "backup"
    # raw crontab contains marker + wrapper, original command intact
    raw = fake_crontab.read_text()
    assert f"# cronicle id={job.id} name=backup" in raw
    assert "cronicle.runner --job-id" in raw or "cronicle-run --job-id" in raw
    assert "/opt/backup.sh" in raw


def test_update_and_delete(fake_crontab):
    job = core.add_job("* * * * *", "echo hi")
    updated = core.update_job(job.id, schedule="5 * * * *", enabled=False)
    assert updated.schedule == "5 * * * *"
    assert not updated.enabled
    assert core.get_job(job.id).schedule == "5 * * * *"
    core.delete_job(job.id)
    assert core.list_jobs() == []


def test_update_unknown_raises(fake_crontab):
    with pytest.raises(core.JobNotFound):
        core.update_job("deadbeef", name="x")
    with pytest.raises(core.JobNotFound):
        core.delete_job("deadbeef")


def test_invalid_schedule_rejected(fake_crontab):
    with pytest.raises(core.InvalidJob):
        core.add_job("not a schedule", "echo x")
    with pytest.raises(core.InvalidJob):
        core.add_job("* * * *", "echo x")  # 4 fields
    with pytest.raises(core.InvalidJob):
        core.add_job("* * * * *", "   ")  # empty command


def test_at_schedule_accepted(fake_crontab):
    job = core.add_job("@daily", "echo hi")
    assert core.get_job(job.id).schedule == "@daily"


def test_preserves_env_and_comments(fake_crontab):
    fake_crontab.write_text("MAILTO=ops@example.com\n# just a comment\n\n0 1 * * * /bin/true\n")
    job = core.add_job("* * * * *", "echo new")
    raw = fake_crontab.read_text()
    assert "MAILTO=ops@example.com" in raw
    assert "# just a comment" in raw
    assert "/bin/true" in raw
    # pre-existing unwrapped job is listed with original command
    olds = [j for j in core.list_jobs() if j.id != job.id]
    assert len(olds) == 1
    assert olds[0].command == "/bin/true"
    assert not olds[0].wrapped and not olds[0].managed


def test_unmanaged_job_gains_marker_on_update(fake_crontab):
    fake_crontab.write_text("0 1 * * * /bin/true\n")
    old = [j for j in core.list_jobs()][0]
    updated = core.update_job(old.id, name="legacy")
    assert updated.managed and updated.wrapped and updated.name == "legacy"
    assert f"# cronicle id={old.id}" in fake_crontab.read_text()


def test_disabled_entry_parsed(fake_crontab):
    fake_crontab.write_text("# 0 2 * * * /opt/nightly.sh\n")
    jobs = core.list_jobs()
    assert len(jobs) == 1
    assert not jobs[0].enabled
    assert jobs[0].command == "/opt/nightly.sh"


def test_plain_comment_not_a_job(fake_crontab):
    fake_crontab.write_text("# run backup every day at five please\n")
    assert core.list_jobs() == []
