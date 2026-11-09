# EnergyPilot

**Turning energy readings into explainable waste alerts.**

A synthetic small office keeps consuming unusually high electricity overnight. The Angular dashboard shows the pattern, its supporting readings, suggested investigation and a cost scenario with editable assumptions. FastAPI performs validation and calculations; SQLite persists readings. There is no LLM, training, live meter connection or autonomous control.

## Run locally

From this directory, Python 3.12+ and Node 24:

```sh
python -m venv .venv
# Activate the environment (Windows: .venv\Scripts\Activate.ps1)
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Each new visitor workspace starts with the synthetic demo so the story is visible immediately. Set `ENERGYPILOT_SEED_DEMO=0` before startup to create empty visitor workspaces, then choose Data & imports → Load synthetic demo if needed. For dashboard development use `npm start` in frontend with the backend running on port 8000; the dev proxy forwards `/api`.

On this prepared Windows checkout, run `./start-demo.ps1` to launch the already-installed, built application. Verification servers are stopped after checking; the script starts a fresh local server.

## Architecture and contract

Angular → same-origin FastAPI API → anonymous workspace cookie → Pydantic row validation → workspace-scoped SQLite transaction. Detection uses Python's median and only earlier observations. Production build is served by FastAPI, avoiding cross-origin configuration and keeping deployment to one service.

CSV header must be exactly `timestamp,kwh`. Each row is one full hour's energy, timestamped at the start of that hour. Values are finite, non-negative interval kWh up to 10,000; timestamps are whole local hours without timezone offsets. These are **not cumulative meter readings**. Each visitor workspace represents one building; the SQLite database holds multiple isolated workspaces. Do not mix sources or buildings within a workspace. Missing intervals remain unknown. DST transition datasets are deliberately unsupported.

Rows with invalid values are rejected individually with CSV row numbers and reasons. Matching existing timestamp/value pairs are skipped. A different value at an existing timestamp is rejected; no silent correction occurs. SQLite's composite primary key guarantees one stored reading per timestamp within each workspace. A transaction groups the valid inserts. Retrying an upload after a lost response is safe because the same readings are deduplicated.

Detection: for hours 00:00–05:59, require at least seven earlier observations by default at the same hour in the prior 14 days. Flag when observed energy is **strictly greater than twice the median** and at least **1 kWh above it**. This is a heuristic pattern, not a confirmed fault. Prior anomalies can contaminate the baseline; holidays, occupancy, meter errors and seasonality are not modelled. The demo deliberately has no DST transition.

The scenario is `sum(flagged observed − baseline) × EUR/kWh × hypothetical avoidable share`. It covers observed flagged intervals only, excludes taxes/standing charges and is neither a bill nor guaranteed savings. Report is downloadable plain text for easy inspection and portability.

## Minimum history configuration

`ENERGYPILOT_MIN_HISTORY` controls how many prior same-hour readings are needed before detection. Default: `7`; accepted integers: `1`–`14`. Invalid values stop startup with a clear error before the database is opened. Lower values allow earlier detection but use less evidence. The 14-day window and other thresholds stay unchanged.

PowerShell, before starting the server:

```powershell
$env:ENERGYPILOT_MIN_HISTORY = "6"
.\start-demo.ps1
```

Restart the server after changing the variable. Remove it with `Remove-Item Env:ENERGYPILOT_MIN_HISTORY` to restore the default on the next startup. The API, dashboard and exported report show the active requirement.

## API

- `POST /api/import`: multipart CSV (`file`); accepted/duplicate/rejected counts and row errors.
- `POST /api/demo`: add synthetic fixture; never clears or overwrites readings.
- `GET /api/overview`: ordered readings, alerts, rule and totals.
- `GET /api/report?tariff=0.25&reduction=100`: downloadable evidence report.
- `GET /api/sample`: sample CSV. Interactive API documentation: `/docs`.

## Verify

```sh
python -m pytest -q
cd frontend
npm ci
npm run build
```

Tests cover invalid dates/units/numbers, duplicate imports, conflicting readings, partial acceptance, minimum history, threshold boundaries, future leakage, daytime exclusions and report calculations. CI runs tests and the Angular build on pushes and PRs once this directory is published as the repository root. CI configuration alone does not mean a hosted run has passed.

## Deploy

See [Railway deployment settings](docs/DEPLOYMENT.md).

Use the Dockerfile with a Python-capable host. Build: `docker build -t energypilot .`. Run: `docker run --rm -p 8000:8000 -v energypilot-data:/var/lib/energypilot energypilot`. The server honours `PORT`. Mount persistent storage at `/var/lib/energypilot`; without it, readings disappear on replacement. Use one application instance for this SQLite demo.

This is an unauthenticated portfolio demo with separate anonymous visitor workspaces. Do not upload personal or production data. The cookie identifies a temporary workspace, not a logged-in user. Whoever possesses that cookie can access its workspace.

## Visitor isolation and expiration

- A random 256-bit cookie identifies the workspace. Only its SHA-256 digest is stored in SQLite; client-supplied workspace IDs are never used as database paths or SQL fragments.
- The cookie is HttpOnly, SameSite=Lax and scoped to `/api`; HTTPS responses also set Secure. API responses containing workspace data use `Cache-Control: no-store`.
- Readings have a composite `(workspace_id, timestamp)` primary key. Imports, duplicate checks, charts, calculations and reports are scoped to the workspace.
- A workspace expires after **24 hours without a data API request**. Requests renew its database timestamp and cookie. Merely viewing a previously loaded tab without making requests does not renew it.
- Expired workspaces and their readings are removed together on application startup or the next data API request. No periodic job keeps the service awake. If the service is idle, expired rows remain on disk until the next cleanup opportunity, but an expired cookie cannot reopen them.
- Reloading or restarting the application preserves a workspace while its cookie, unexpired database record and persistent volume remain available. Clearing the cookie or returning after expiry starts a fresh synthetic workspace. Different browser profiles receive separate workspaces; tabs in one profile share one.
- Demo storage is bounded to **200 active workspaces** and **10,000 readings per workspace**, including the 384 seeded readings. At workspace capacity, new visitors get HTTP 503; existing visitors retain access. A quota-exceeding import gets HTTP 413 and rolls back all its inserts. Identical repeated imports still succeed at the quota.
- Existing shared `readings` tables from earlier versions are left untouched and are not exposed or copied into visitor workspaces. New data uses `workspaces` and `workspace_readings`. Back up any old local data before manual removal.

Limitations: one application instance, no accounts, no cross-device recovery, no per-IP abuse controls. Storage limits bound growth but do not prevent someone occupying all workspace slots. This is demo isolation, not a production identity or access-control system.

## Demo

See the [60-second walkthrough](docs/WALKTHROUGH.md) for a reproducible demo script. Public deployment and a recorded video are still outstanding.

See [architectural decisions and interview prompts](docs/DECISIONS.md).

## Verification record — 2026-11-09

- Angular production build passed (Node 24.14.0).
- Local HTTP checks returned 200 for the dashboard, overview and downloadable report. Fixture: 384 readings, 12 flagged hours, 45.45 kWh observed excess.
- 40 automated tests passed on Python 3.14, including visitor isolation, expiration and workspace limits. The dependency combination emits a Starlette warning that the httpx TestClient adapter is deprecated; tests currently work. The Python 3.12 CI/container configuration still needs a hosted run.
- Browser automation could not initialise in this environment. Visual layout, actual browser interactions and responsive rendering have not been verified.
- Public hosting and a recorded walkthrough are outstanding. The walkthrough document is a script, not a video. Docker configuration is provided but has not been built here.

## Technical references

- [Angular standalone bootstrap](https://angular.dev/api/platform-browser/bootstrapApplication)
- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)
