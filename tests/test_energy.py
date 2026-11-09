from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from backend.main import app, analyse


@pytest.fixture(autouse=True)
def default_history_environment(monkeypatch):
    monkeypatch.delenv("ENERGYPILOT_MIN_HISTORY", raising=False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENERGYPILOT_DB", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("ENERGYPILOT_SEED_DEMO", "0")
    with TestClient(app) as client:
        yield client


def upload(client, text):
    return client.post("/api/import", files={"file": ("readings.csv", text, "text/csv")})


def test_repeat_demo_is_idempotent(client):
    first = client.post("/api/demo").json()
    second = client.post("/api/demo").json()
    data = client.get("/api/overview").json()
    assert first["accepted"] == 384
    assert second["accepted"] == 0 and second["duplicates"] == 384
    assert len(data["readings"]) == 384
    assert len(data["alerts"]) == 12
    # Exact fixture expectation includes the varying same-hour historical medians.
    assert data["excess_kwh"] == pytest.approx(45.45)


def test_partial_import_and_conflict_preserve_original(client):
    result = upload(client, "timestamp,kwh\n2026-10-01T00:00:00,1.2\n2026-10-01T01:00:00,-1\nwrong,2\n").json()
    assert (result["accepted"], result["rejected"]) == (1, 2)
    assert [e["row"] for e in result["errors"]] == [3, 4]
    conflict = upload(client, "timestamp,kwh\n2026-10-01T00:00:00,99\n").json()
    assert conflict["rejected"] == 1
    assert client.get("/api/overview").json()["readings"][0]["kwh"] == 1.2


@pytest.mark.parametrize("row", ["bad,1", "2026-10-01T00:30:00,1", "2026-10-01T00:00:00Z,1", "2026-10-01T00:00:00,nan", "2026-10-01T00:00:00,inf", "2026-10-01T00:00:00,-0.1"])
def test_invalid_values_have_reasons(client, row):
    result = upload(client, "timestamp,kwh\n" + row).json()
    assert result["rejected"] == 1 and result["errors"][0]["reason"]
    assert not client.get("/api/overview").json()["readings"]


def test_header_and_encoding_errors(client):
    assert upload(client, "date,value\n").status_code == 422
    assert upload(client, b"\xff\xfe").status_code == 422


def history(values):
    return [{"timestamp": (datetime(2026, 10, 1) + timedelta(days=i)).isoformat(), "kwh": v} for i, v in enumerate(values)]


def test_detection_boundaries_and_minimum_history():
    assert not analyse(history([1] * 6 + [9]))
    assert not analyse(history([1] * 7 + [2]))  # exactly 2x is not >2x
    assert not analyse(history([.2] * 7 + [1.1]))  # excess below 1 kWh
    alerts = analyse(history([1] * 7 + [3]))
    assert alerts == [{"timestamp": "2026-10-08T00:00:00", "kwh": 3, "baseline": 1, "excess": 2, "reference_count": 7}]


def test_no_future_leakage_or_daytime_alerts():
    rows = history([1] * 7 + [3])
    assert analyse(rows + history([100] * 30)[20:])[:1] == analyse(rows)
    assert not analyse([{**r, "timestamp": r["timestamp"].replace("T00:", "T12:")} for r in rows])


def test_report_and_scenario_validation(client):
    client.post("/api/demo")
    response = client.get("/api/report?tariff=0.25&reduction=50")
    assert response.status_code == 200
    assert "EUR 5.68" in response.text and "Synthetic" in response.text
    assert "attachment" in response.headers["content-disposition"]
    assert client.get("/api/report?tariff=-1").status_code == 422
    assert client.get("/api/report?reduction=101").status_code == 422


def test_large_file_is_rejected(client):
    assert upload(client, "x" * 2_000_001).status_code == 413


def test_row_limit_rolls_back_valid_inserts(client):
    start = datetime(2020, 1, 1)
    text = "timestamp,kwh\n" + "\n".join(f"{(start+timedelta(hours=i)).isoformat()},1" for i in range(10001))
    assert upload(client, text).status_code == 413
    assert client.get("/api/overview").json()["readings"] == []


def test_old_history_is_not_used():
    old = history([1] * 7)
    target = {"timestamp": "2026-11-01T00:00:00", "kwh": 20}
    assert not analyse(old + [target])


def test_fresh_demo_bootstrap_is_repeatable(client, monkeypatch):
    monkeypatch.setenv("ENERGYPILOT_SEED_DEMO", "1")
    with TestClient(app) as seeded:
        assert len(seeded.get("/api/overview").json()["readings"]) == 384
    with TestClient(app) as repeated:
        assert len(repeated.get("/api/overview").json()["readings"]) == 384


@pytest.mark.parametrize("minimum, expected_count", [(7, 0), (6, 1)])
def test_configured_history_controls_detection_and_report(tmp_path, monkeypatch, minimum, expected_count):
    monkeypatch.setenv("ENERGYPILOT_DB", str(tmp_path / "configured.sqlite3"))
    monkeypatch.setenv("ENERGYPILOT_SEED_DEMO", "0")
    monkeypatch.setenv("ENERGYPILOT_MIN_HISTORY", str(minimum))
    rows = history([1] * 6 + [3])
    text = "timestamp,kwh\n" + "\n".join(f"{row['timestamp']},{row['kwh']}" for row in rows)
    with TestClient(app) as client:
        assert upload(client, text).json()["accepted"] == 7
        data = client.get("/api/overview").json()
        assert data["min_history"] == minimum
        assert len(data["alerts"]) == expected_count
        assert f"≥{minimum} reference observations" in data["rule"]
        assert f"≥{minimum} reference observations" in client.get("/api/report").text
        if expected_count:
            assert data["alerts"][0]["reference_count"] == 6
            assert data["alerts"][0]["excess"] == 2
        # Configuration is a startup setting, not re-read midway through requests.
        monkeypatch.setenv("ENERGYPILOT_MIN_HISTORY", "14")
        assert client.get("/api/overview").json()["min_history"] == minimum


@pytest.mark.parametrize("raw", ["0", "15", "-1", "six", "6.5", ""])
def test_invalid_history_prevents_startup(tmp_path, monkeypatch, raw):
    database = tmp_path / "must-not-exist.sqlite3"
    monkeypatch.setenv("ENERGYPILOT_DB", str(database))
    monkeypatch.setenv("ENERGYPILOT_MIN_HISTORY", raw)
    with pytest.raises(ValueError, match="ENERGYPILOT_MIN_HISTORY must be an integer from 1 to 14"):
        with TestClient(app):
            pass
    assert not database.exists()


@pytest.mark.parametrize("minimum", [1, 14])
def test_history_range_endpoints(client, monkeypatch, minimum):
    monkeypatch.setenv("ENERGYPILOT_MIN_HISTORY", str(minimum))
    with TestClient(app) as configured:
        assert configured.get("/api/overview").json()["min_history"] == minimum


def test_default_history_is_seven(client):
    assert client.get("/api/overview").json()["min_history"] == 7
