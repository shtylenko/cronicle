import pytest
from fastapi.testclient import TestClient

from cronicle.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CRONICLE_FAKE_CRONTAB", str(tmp_path / "crontab"))
    monkeypatch.setenv("CRONICLE_DATA_DIR", str(tmp_path / "data"))
    return TestClient(app)


def test_job_crud_and_run_flow(client):
    r = client.post("/api/jobs", json={"name": "t", "schedule": "* * * * *",
                                      "command": "echo api", "description": "Test job"})
    assert r.status_code == 201
    job_id = r.json()["id"]
    assert r.json()["schedule_human"] == "Every minute"
    assert r.json()["description"] == "Test job"

    assert client.get("/api/jobs").json()[0]["command"] == "echo api"

    r = client.put(f"/api/jobs/{job_id}", json={"schedule": "5 * * * *"})
    assert r.status_code == 200
    assert r.json()["schedule"] == "5 * * * *"
    assert r.json()["description"] == "Test job"  # preserved when not patched

    r = client.post(f"/api/jobs/{job_id}/run")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    runs = client.get("/api/runs", params={"job_id": job_id}).json()
    assert len(runs) == 1
    log = client.get(f"/api/runs/{runs[0]['id']}/log").json()
    assert "api" in log["text"]

    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get("/api/jobs").json() == []
    # history survives job deletion
    assert len(client.get("/api/runs", params={"job_id": job_id}).json()) == 1


def test_validation_and_404s(client):
    assert client.post("/api/jobs", json={"schedule": "bogus", "command": "x"}).status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/log").status_code == 404


def test_health_and_index(client):
    assert client.get("/api/health").json()["ok"] is True
    r = client.get("/")
    assert r.status_code == 200
    assert "app.js?v=" in r.text and "styles.css?v=" in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_parse_schedule(client):
    r = client.post("/api/parse-schedule", json={"text": "Every Wednesday at 5pm"})
    assert r.status_code == 200
    assert r.json() == {"schedule": "0 17 * * 3"}
    r = client.post("/api/parse-schedule", json={"text": "sometime-ish"})
    assert r.status_code == 400


def test_projects_crud_and_job_linking(client):
    assert client.get("/api/projects").json() == []
    r = client.post("/api/projects", json={"name": "trading"})
    assert r.status_code == 201
    assert client.post("/api/projects", json={"name": "trading"}).status_code == 409
    assert client.post("/api/projects", json={"name": "bad/name"}).status_code == 400

    # unknown project rejected on assignment
    r = client.post("/api/jobs", json={"schedule": "* * * * *", "command": "echo x",
                                       "project": "nope"})
    assert r.status_code == 400
    r = client.post("/api/jobs", json={"schedule": "* * * * *", "command": "echo x",
                                       "project": "trading"})
    assert r.status_code == 201
    job_id = r.json()["id"]
    assert r.json()["project"] == "trading"

    assert client.get("/api/projects").json()[0]["job_count"] == 1
    assert len(client.get("/api/jobs", params={"project": "trading"}).json()) == 1
    assert client.get("/api/jobs", params={"project": ""}).json() == []

    # rename propagates to jobs
    r = client.put("/api/projects/trading", json={"name": "markets"})
    assert r.status_code == 200
    assert r.json()["job_count"] == 1
    assert client.get(f"/api/jobs/{job_id}").json()["project"] == "markets"

    # delete blocked while linked, allowed after unlink
    assert client.delete("/api/projects/markets").status_code == 409
    assert client.put(f"/api/jobs/{job_id}", json={"project": ""}).status_code == 200
    assert client.delete("/api/projects/markets").status_code == 204
    assert client.delete("/api/projects/markets").status_code == 404


def test_runs_filtered_by_project(client):
    client.post("/api/projects", json={"name": "a"})
    client.post("/api/projects", json={"name": "b"})
    ja = client.post("/api/jobs", json={"schedule": "* * * * *", "command": "echo a",
                                        "project": "a"}).json()["id"]
    jb = client.post("/api/jobs", json={"schedule": "* * * * *", "command": "echo b",
                                        "project": "b"}).json()["id"]
    client.post(f"/api/jobs/{ja}/run")
    client.post(f"/api/jobs/{jb}/run")
    runs = client.get("/api/runs", params={"project": "a"}).json()
    assert [r["job_id"] for r in runs] == [ja]
    assert len(client.get("/api/runs").json()) == 2
