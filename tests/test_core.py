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


def test_description_round_trip(fake_crontab):
    job = core.add_job("0 2 * * *", "/opt/backup.sh", name="backup",
                       description="Nightly backup of trading database")
    assert job.description == "Nightly backup of trading database"
    assert "# cronicle:desc Nightly backup of trading database" in fake_crontab.read_text()
    assert core.get_job(job.id).description == "Nightly backup of trading database"


def test_description_update_and_clear(fake_crontab):
    job = core.add_job("* * * * *", "echo hi")
    assert core.get_job(job.id).description == ""
    updated = core.update_job(job.id, description="Says hi every minute")
    assert updated.description == "Says hi every minute"
    # update without description preserves it
    assert core.update_job(job.id, name="hi").description == "Says hi every minute"
    # empty string clears it
    cleared = core.update_job(job.id, description="")
    assert cleared.description == ""
    assert "cronicle:desc" not in fake_crontab.read_text()


def test_multiline_description_round_trip(fake_crontab):
    job = core.add_job("0 0 * * *", "echo x", description="line one\nline two")
    assert core.get_job(job.id).description == "line one\nline two"
    raw = fake_crontab.read_text()
    assert "# cronicle:desc line one\n# cronicle:desc line two\n" in raw


def test_orphan_marker_and_desc_preserved(fake_crontab):
    fake_crontab.write_text("# cronicle id=abc123 name=orphan\n# cronicle:desc no entry below\n")
    assert core.list_jobs() == []
    assert fake_crontab.read_text() == (
        "# cronicle id=abc123 name=orphan\n# cronicle:desc no entry below\n")


def test_desc_without_marker_stays_comment(fake_crontab):
    fake_crontab.write_text("# cronicle:desc stray line\n0 1 * * * /bin/true\n")
    jobs = core.list_jobs()
    assert len(jobs) == 1 and jobs[0].description == ""


def test_project_round_trip(fake_crontab):
    job = core.add_job("0 2 * * *", "/opt/digest.sh", name="digest", project="trading")
    assert job.project == "trading"
    assert "# cronicle:project trading" in fake_crontab.read_text()
    assert core.get_job(job.id).project == "trading"


def test_project_update_and_clear(fake_crontab):
    job = core.add_job("* * * * *", "echo hi")
    assert core.get_job(job.id).project == ""
    assert core.update_job(job.id, project="trading").project == "trading"
    assert core.update_job(job.id, name="hi").project == "trading"  # preserved
    assert core.update_job(job.id, project="").project == ""
    assert "cronicle:project" not in fake_crontab.read_text()


def test_project_and_desc_coexist(fake_crontab):
    job = core.add_job("0 0 * * *", "echo x", project="trading", description="nightly x")
    got = core.get_job(job.id)
    assert (got.project, got.description) == ("trading", "nightly x")


def test_unknown_aux_line_preserved_across_update(fake_crontab):
    fake_crontab.write_text(
        "# cronicle id=abc123 name=x\n# cronicle:future something-new\n0 1 * * * /bin/true\n")
    assert core.list_jobs()[0].id == "abc123"
    core.update_job("abc123", name="y")
    assert "# cronicle:future something-new" in fake_crontab.read_text()


def test_rename_project_refs(fake_crontab):
    a = core.add_job("* * * * *", "echo a", project="old")
    b = core.add_job("* * * * *", "echo b", project="old", description="keep me")
    c = core.add_job("* * * * *", "echo c", project="other")
    assert core.rename_project_refs("old", "new") == 2
    assert core.get_job(a.id).project == "new"
    assert core.get_job(b.id).project == "new"
    assert core.get_job(b.id).description == "keep me"
    assert core.get_job(c.id).project == "other"


def test_validate_project_name():
    assert core.validate_project_name("trading") == "trading"
    assert core.validate_project_name("  my-proj_2.0 ") == "my-proj_2.0"
    for bad in ["", "  ", "a/b", "a\nb", "x" * 61, "semi;colon"]:
        with pytest.raises(core.InvalidJob):
            core.validate_project_name(bad)
