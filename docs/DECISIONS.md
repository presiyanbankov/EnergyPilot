# Decisions to defend

| Decision | Reason | Cost / limitation |
| --- | --- | --- |
| Interval kWh, whole local hours | Simple, inspectable CSV contract | No cumulative meters, irregular intervals or DST transition datasets |
| Same-hour historical median | Robust to isolated extreme references and easy to explain | Not weather- or occupancy-adjusted; persistent anomalies eventually shift baseline |
| Prior-only reference window | Prevent target/future observations leaking into its baseline | Requires 7 references by default; configurable from 1 to 14 at startup |
| Two thresholds | Relative change and absolute excess suppress small numerical fluctuations | Heuristic values are uncalibrated, not accuracy claims |
| SQLite workspace + timestamp primary key | Repeat imports are safe and visitors cannot change each other’s readings | One building per workspace; legitimate corrections need a separate audited workflow |
| Immediate import transaction | Duplicate check and insert cannot race between writers | Writers serialise; not appropriate for high-volume distributed ingestion |
| Partial row acceptance | Useful CSV errors without discarding all valid data | Counts must be inspected; a completed request can contain rejections |
| Transaction rollback for file-level failure | Oversized/malformed file cannot leave unknown partial state | Row validation failures are different: intentionally reported and skipped |
| Single-origin Angular + FastAPI | One deployable service with real backend results | Static Sites hosting alone cannot run this Python backend |
| Downloadable plain text report | Portable, searchable and easy to inspect | Not a typeset PDF |
| No ML | Transparent deterministic rule is sufficient for the demo | No evaluated forecasting or predictive capability claimed |

Detection maintains a separate bounded chronological history for each overnight hour. It sorts readings once and considers at most 14 prior daily observations per hour for the supported hourly contract, rather than rescanning all readings for every target. Returned alerts carry the reference count and baseline for inspection.

## Interview prompts

- What happens if a browser loses the upload response after the transaction commits?
- Why does a conflicting timestamp get rejected instead of overwritten?
- Why is an anomaly not a confirmed waste diagnosis?
- How does the threshold behave at exactly twice the baseline?
- Why is a scenario estimate not an annual savings forecast?
- What changes for multiple buildings, DST, cumulative readings or multiple application replicas?

## Anonymous visitor workspaces

One SQLite file stores server-issued workspace IDs and scoped readings. A random cookie is checked against the stored token digest; unknown or malformed cookies get new workspaces. Every data query includes the server-resolved workspace ID, and foreign keys cascade deletion to its readings.

Sliding 24-hour expiry preserves a returning visitor's imports without storing them indefinitely. Cleanup runs at startup and on data requests, rather than a scheduled background task that might interfere with hosting sleep. Deletion is lazy: idle disk cleanup waits until the next opportunity. Active workspaces are never evicted to make space for new visitors.

The cookie is a bearer credential for disposable demo data, not an account. Clearing cookies loses access. Workspaces require the same SQLite volume after redeployment. Quotas are deliberately modest for a small public demonstration; they do not replace abuse protection.
