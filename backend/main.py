"""EnergyPilot: single-building, hourly interval energy investigation."""
import csv
import io
import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from .workspaces import (COOKIE_NAME, MAX_READINGS, WORKSPACE_TTL_SECONDS,
                         WorkspaceCapacityError, cleanup_expired, connect, now, resolve_workspace)

ROOT = Path(__file__).resolve().parents[1]


def load_min_history():
    raw = os.environ.get("ENERGYPILOT_MIN_HISTORY", "7")
    message = "ENERGYPILOT_MIN_HISTORY must be an integer from 1 to 14"
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(message) from None
    if not 1 <= value <= 14:
        raise ValueError(message)
    return value


@asynccontextmanager
async def lifespan(app):
    app.state.min_history = load_min_history()
    app.state.seed_rows = []
    if os.environ.get("ENERGYPILOT_SEED_DEMO", "1") == "1":
        reader = csv.DictReader(io.StringIO((ROOT / "data" / "synthetic-office.csv").read_text(encoding="utf-8")))
        readings = [Reading.model_validate(row) for row in reader]
        app.state.seed_rows = [(r.timestamp.isoformat(), r.kwh) for r in readings]
    with connect() as db:
        cleanup_expired(db, now())
    yield


app = FastAPI(title="EnergyPilot", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def visitor_workspace(request: Request, call_next):
    scoped_routes = {"/api/overview", "/api/import", "/api/demo", "/api/report"}
    if request.url.path.rstrip("/") not in scoped_routes:
        return await call_next(request)
    try:
        cookie, workspace_id = await run_in_threadpool(
            resolve_workspace, request.cookies.get(COOKIE_NAME), app.state.seed_rows)
    except WorkspaceCapacityError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503,
                            headers={"Cache-Control": "no-store", "Retry-After": "300"})
    request.state.workspace_id = workspace_id
    response = await call_next(request)
    # Applies to JSON and downloadable Response objects, including row-validation errors.
    response.set_cookie(COOKIE_NAME, cookie, max_age=WORKSPACE_TTL_SECONDS,
                        httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/api")
    response.headers["Cache-Control"] = "no-store"
    return response


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    timestamp: datetime
    kwh: float = Field(ge=0, le=10000, allow_inf_nan=False)

    @field_validator("timestamp")
    @classmethod
    def hourly_local_time(cls, value):
        if value.tzinfo is not None or value.minute or value.second or value.microsecond:
            raise ValueError("Use a whole local hour without timezone: YYYY-MM-DDTHH:00:00")
        return value


def import_text(text, workspace_id):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["timestamp", "kwh"]:
        raise HTTPException(422, "CSV header must be exactly timestamp,kwh; readings are hourly interval kWh.")
    accepted = duplicates = 0
    errors = []
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")  # Serialise duplicate check + insert across upload requests.
        stored_count = db.execute("SELECT COUNT(*) FROM workspace_readings WHERE workspace_id=?", (workspace_id,)).fetchone()[0]
        try:
            for number, row in enumerate(reader, 2):
                if number > 10001:
                    raise HTTPException(413, "Maximum 10,000 readings per import")
                try:
                    reading = Reading.model_validate(row)
                except ValidationError as exc:
                    errors.append({"row": number, "reason": "; ".join(f"{'.'.join(map(str,e['loc']))}: {e['msg']}" for e in exc.errors())})
                    continue
                timestamp = reading.timestamp.isoformat()
                previous = db.execute("SELECT kwh FROM workspace_readings WHERE workspace_id=? AND timestamp=?", (workspace_id, timestamp)).fetchone()
                if previous:
                    if previous["kwh"] == reading.kwh:
                        duplicates += 1
                    else:
                        errors.append({"row": number, "reason": "Timestamp already exists with a different kWh value; original retained"})
                else:
                    if stored_count + accepted >= MAX_READINGS:
                        raise HTTPException(413, "Workspace limit is 10,000 stored readings; this import was rolled back")
                    db.execute("INSERT INTO workspace_readings VALUES (?, ?, ?)", (workspace_id, timestamp, reading.kwh))
                    accepted += 1
        except csv.Error as exc:
            raise HTTPException(422, f"Malformed CSV: {exc}") from exc
    return {"accepted": accepted, "duplicates": duplicates, "rejected": len(errors), "errors": errors}


def analyse(rows, min_history=7):
    # The reference window contains only observations strictly before the target.
    alerts = []
    hours = defaultdict(deque)
    for row in sorted(rows, key=lambda r: r["timestamp"]):
        at = datetime.fromisoformat(row["timestamp"])
        if at.hour >= 6:
            continue
        window = hours[at.hour]
        while window and at - window[0][0] > timedelta(days=14):
            window.popleft()
        history = [value for stamp, value in window if stamp < at]
        window.append((at, row["kwh"]))
        if len(history) < min_history:
            continue
        baseline = median(history)
        if row["kwh"] > baseline * 2 and row["kwh"] - baseline >= 1:
            alerts.append({"timestamp": row["timestamp"], "kwh": row["kwh"], "baseline": round(baseline, 4),
                           "excess": round(row["kwh"] - baseline, 4), "reference_count": len(history)})
    return alerts


@app.post("/api/import")
async def import_csv(request: Request, file: UploadFile = File(...)):
    raw = await file.read(2_000_001)
    if len(raw) > 2_000_000:
        raise HTTPException(413, "CSV must be at most 2 MB")
    try:
        return import_text(raw.decode("utf-8-sig"), request.state.workspace_id)
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Please upload a UTF-8 CSV file") from exc


@app.get("/api/overview")
def overview(request: Request):
    with connect() as db:
        rows = [dict(row) for row in db.execute("SELECT timestamp,kwh FROM workspace_readings WHERE workspace_id=? ORDER BY timestamp", (request.state.workspace_id,))]
    min_history = app.state.min_history
    alerts = analyse(rows, min_history=min_history)
    return {"building": "Linden Office", "data_label": "Synthetic demonstration data / local building time",
            "readings": rows, "alerts": alerts, "min_history": min_history, "total_kwh": round(sum(r["kwh"] for r in rows), 3),
            "excess_kwh": round(sum(a["excess"] for a in alerts), 3),
            "rule": f"00:00–05:59; >2× median of prior 14 days at the same hour; ≥1 kWh excess; ≥{min_history} reference observations"}


@app.post("/api/demo")
def load_demo(request: Request):
    # Add-only: never clears or overwrites visitor data.
    return import_text((ROOT / "data" / "synthetic-office.csv").read_text(encoding="utf-8"), request.state.workspace_id)


@app.get("/api/sample")
def sample():
    return Response((ROOT / "data" / "synthetic-office.csv").read_text(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="synthetic-office.csv"'})


@app.get("/api/report")
def report(request: Request, tariff: float = Query(0.25, ge=0, le=10, allow_inf_nan=False), reduction: float = Query(100, ge=0, le=100, allow_inf_nan=False)):
    data = overview(request)
    estimate = data["excess_kwh"] * tariff * reduction / 100
    output = ["ENERGYPILOT — INVESTIGATION REPORT", data["building"], data["data_label"], "",
              f"Readings: {len(data['readings'])}; flagged hours: {len(data['alerts'])}",
              f"Observed excess above baseline: {data['excess_kwh']:.2f} kWh", data["rule"], "",
              f"Scenario: EUR {tariff:.4f}/kWh × {data['excess_kwh']:.3f} kWh × {reduction:.1f}% = EUR {estimate:.2f}",
              "Illustrative avoidable cost for observed flagged intervals only; not a bill or guaranteed saving.",
              "Investigate HVAC schedules, cleaning hours, server loads and meter configuration; confirm occupancy before changing settings.",
              "Missing intervals are unknown, never zero. Local-time contract excludes DST transition datasets.", "", "EVIDENCE"]
    output += [f"{a['timestamp']} | observed {a['kwh']:.2f} kWh | baseline {a['baseline']:.2f} | excess {a['excess']:.2f} | references {a['reference_count']}" for a in data["alerts"]]
    return Response("\n".join(output), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="energypilot-report.txt"'})


dist = ROOT / "frontend" / "dist" / "energypilot" / "browser"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
