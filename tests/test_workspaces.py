import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend import main, workspaces
from backend.workspaces import COOKIE_NAME, WORKSPACE_TTL_SECONDS, connect

EXTRA = "timestamp,kwh\n2026-11-01T00:00:00,12\n"


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("ENERGYPILOT_DB", str(tmp_path / "visitors.sqlite3"))
    monkeypatch.setenv("ENERGYPILOT_SEED_DEMO", "1")
    monkeypatch.delenv("ENERGYPILOT_MIN_HISTORY", raising=False)


def upload(client, text=EXTRA):
    return client.post("/api/import", files={"file": ("readings.csv", text, "text/csv")})


def test_new_visitors_get_independent_seeded_workspaces():
    with TestClient(app) as alice, TestClient(app) as bob:
        a = alice.get("/api/overview").json()
        b = bob.get("/api/overview").json()
        assert len(a["readings"]) == len(b["readings"]) == 384
        assert alice.cookies.get(COOKIE_NAME) != bob.cookies.get(COOKIE_NAME)
        assert upload(alice).json()["accepted"] == 1
        assert len(alice.get("/api/overview").json()["readings"]) == 385
        assert len(bob.get("/api/overview").json()["readings"]) == 384
        assert "Readings: 385" in alice.get("/api/report").text
        assert "Readings: 384" in bob.get("/api/report").text
        # The same timestamp is independent in Bob's workspace.
        assert upload(bob).json()["accepted"] == 1
        assert upload(alice).json()["duplicates"] == 1
        assert alice.post("/api/demo").json()["duplicates"] == 384
        conflict = upload(alice, EXTRA.replace(",12", ",99")).json()
        assert conflict["rejected"] == 1
        assert bob.get("/api/overview").json()["readings"][-1]["kwh"] == 12


def test_cookie_reuses_workspace_after_client_restart():
    with TestClient(app) as first:
        assert upload(first).json()["accepted"] == 1
        cookie = first.cookies.get(COOKIE_NAME)
    with TestClient(app) as reopened:
        reopened.cookies.set(COOKIE_NAME, cookie, domain="testserver.local", path="/api")
        assert len(reopened.get("/api/overview").json()["readings"]) == 385
        assert reopened.cookies.get(COOKIE_NAME) == cookie


def test_sliding_expiration_removes_readings_and_replaces_cookie(monkeypatch):
    clock = [workspaces.now()]
    monkeypatch.setattr(workspaces, "now", lambda: clock[0])
    with TestClient(app) as client:
        upload(client)
        original = client.cookies.get(COOKIE_NAME)
        old_id = hashlib.sha256(original.encode()).hexdigest()
        for _ in range(2):
            clock[0] += WORKSPACE_TTL_SECONDS - 1
            assert len(client.get("/api/overview").json()["readings"]) == 385
            assert client.cookies.get(COOKIE_NAME) == original
        clock[0] += WORKSPACE_TTL_SECONDS
        assert len(client.get("/api/overview").json()["readings"]) == 384
        assert client.cookies.get(COOKIE_NAME) != original
        with connect() as db:
            assert db.execute("SELECT COUNT(*) FROM workspaces WHERE id=?", (old_id,)).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM workspace_readings WHERE workspace_id=?", (old_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("cookie", ["../energy.sqlite3", "x" * 43, "", "x" * 500])
def test_unknown_or_malformed_cookie_creates_fresh_workspace(cookie):
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, cookie, domain="testserver.local", path="/api")
        response = client.get("/api/overview")
        assert len(response.json()["readings"]) == 384
        assert client.cookies.get(COOKIE_NAME) != cookie


def test_cookie_and_cache_headers_on_report_and_json():
    with TestClient(app, base_url="https://testserver") as client:
        report = client.get("/api/report")
        cookie = report.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        assert "path=/api" in cookie and "max-age=86400" in cookie
        assert report.headers["cache-control"] == "no-store"
        assert "Readings: 384" in report.text
        assert client.get("/api/overview").headers["cache-control"] == "no-store"
        failed = upload(client, "wrong,header\n")
        assert failed.status_code == 422
        assert failed.headers["cache-control"] == "no-store"


def test_capacity_preserves_active_workspaces_and_recovers_on_expiry(monkeypatch):
    monkeypatch.setattr(workspaces, "MAX_WORKSPACES", 1)
    clock = [workspaces.now()]
    monkeypatch.setattr(workspaces, "now", lambda: clock[0])
    with TestClient(app) as existing, TestClient(app) as newcomer:
        upload(existing)
        busy = newcomer.get("/api/overview")
        assert busy.status_code == 503 and busy.headers["retry-after"] == "300"
        assert len(existing.get("/api/overview").json()["readings"]) == 385
        clock[0] += WORKSPACE_TTL_SECONDS
        assert newcomer.get("/api/overview").status_code == 200


def test_workspace_row_limit_rolls_back_and_allows_repeat_import(monkeypatch):
    monkeypatch.setattr(main, "MAX_READINGS", 385)
    with TestClient(app) as client:
        # 384 seeded rows leave room for only one of these two rows.
        text = EXTRA + "2026-11-01T01:00:00,4\n"
        assert upload(client, text).status_code == 413
        assert len(client.get("/api/overview").json()["readings"]) == 384
        assert upload(client).json()["accepted"] == 1
        assert upload(client).json()["duplicates"] == 1


def test_simultaneous_repeat_upload_cannot_duplicate_readings():
    with TestClient(app) as first, TestClient(app) as second:
        first.get("/api/overview")
        cookie = first.cookies.get(COOKIE_NAME)
        second.cookies.set(COOKIE_NAME, cookie, domain="testserver.local", path="/api")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(upload, [first, second]))
        assert all(result.status_code == 200 for result in results)
        assert sorted(result.json()["accepted"] for result in results) == [0, 1]
        assert sum(result.json()["duplicates"] for result in results) == 1
        assert len(first.get("/api/overview").json()["readings"]) == 385


def test_legacy_shared_readings_are_not_exposed():
    with connect() as db:
        db.execute("CREATE TABLE readings(timestamp TEXT PRIMARY KEY, kwh REAL NOT NULL)")
        db.execute("INSERT INTO readings VALUES ('2026-11-01T00:00:00', 999)")
    with TestClient(app) as client:
        data = client.get("/api/overview").json()
        assert len(data["readings"]) == 384
        assert all(row["kwh"] != 999 for row in data["readings"])


def test_startup_cleans_expired_workspace_without_a_data_request():
    with TestClient(app) as client:
        upload(client)
    with connect() as db:
        db.execute("UPDATE workspaces SET last_seen=?", (workspaces.now() - WORKSPACE_TTL_SECONDS - 1,))
    with TestClient(app):
        with connect() as db:
            assert db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM workspace_readings").fetchone()[0] == 0


def test_healthcheck_does_not_create_workspaces_even_at_capacity(monkeypatch):
    monkeypatch.setattr(workspaces, "MAX_WORKSPACES", 0)
    with TestClient(app) as client:
        for _ in range(3):
            response = client.get("/api/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert "set-cookie" not in response.headers
        assert client.get("/api/overview").status_code == 503
        with connect() as db:
            assert db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 0
