# Railway deployment

EnergyPilot runs as one Docker service with one replica. `railway.json` selects the Dockerfile, deployment health check and automatic sleeping. It requires a persistent volume at `/var/lib/energypilot` before deployment.

Service settings:

- Source: `presiyanbankov/EnergyPilot`, branch `main`, repository root.
- Volume mount: `/var/lib/energypilot`.
- `ENERGYPILOT_DB=/var/lib/energypilot/energy.sqlite3` (also the Dockerfile default).
- `ENERGYPILOT_SEED_DEMO=1` and `ENERGYPILOT_MIN_HISTORY=7`.
- `RAILWAY_RUN_UID=0`: Railway mounts volumes as root; this permits SQLite to write to the mounted directory despite the image's default non-root user.
- `FORWARDED_ALLOW_IPS=*`: trust the Railway edge proxy's forwarded protocol so HTTPS responses issue Secure cookies. Use only behind a trusted proxy.
- Generate a Railway public domain targeting the platform-provided `PORT`. The Docker start command binds `0.0.0.0` and reads `PORT`.

`/api/health` checks database connectivity without creating a visitor workspace. Startup already checks configuration and initializes storage. Railway's deployment health check is not a continuous uptime monitor.

Post-deployment checks: dashboard and sample download; two visitor sessions start with synthetic readings; upload affects only its own workspace; report contains the active assumptions; HTTPS cookies are HttpOnly, Secure and SameSite=Lax. Restart/redeploy and verify the same visitor cookie can still access its persisted workspace.

Automatic sleeping can cause a cold start and sometimes a failed first request. Volume storage can still incur charges while compute sleeps. Do not add uptime pings that continually wake the service. Measure actual Railway usage; no fixed monthly cost is promised.

References: https://docs.railway.com/volumes, https://docs.railway.com/config-as-code/reference, https://docs.railway.com/deployments/serverless.
