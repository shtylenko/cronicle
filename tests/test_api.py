import pytest
from fastapi.testclient import TestClient

from cronicle.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CRONICLE_FAKE_CRONTAB", str(tmp_path / "crontab"))
    monkeypatch.setenv("CRONICLE_DATA_DIR", str(tmp_path / "data"))
    return TestClient(app)


def test_job_crud_and_run_flow(client):
    r = client.post("/api/jobs", json={"name": "t", "schedule": "* * * * *", "command": "echo api"})
    assert r.status_code == 201
    job_id = r.json()["id"]

    assert client.get("/api/jobs").json()[0]["command"] == "echo api"

    r = client.put(f"/api/jobs/{job_id}", json={"schedule": "5 * * * *"})
    assert r.status_code == 200
    assert r.json()["schedule"] == "5 * * * *"

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
    assert client.get("/").status_code == 200


def test_parse_schedule(client):
    r = client.post("/api/parse-schedule", json={"text": "Every Wednesday at 5pm"})
    assert r.status_code == 200
    assert r.json() == {"schedule": "0 17 * * 3"}
    r = client.post("/api/parse-schedule", json={"text": "sometime-ish"})
    assert r.status_code == 400
