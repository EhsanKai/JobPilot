# Live source validation reports

Evidence from `python -m e2e.live_sources` (see `docs/job-intelligence.md` §22).
Each report is the harness's raw JSON output: probes (reachability, HTTP
status, response shape), discovery summaries and analysis when a provider
was reachable. Reports are never edited by hand.

| Date | Environment | Status | Notes |
|---|---|---|---|
| 2026-10-06 | Claude Code cloud container (V0.4.1 development) | BLOCKED | `2026-10-06-environment-blocked.json`: egress proxy denied `CONNECT` (403, "no rule or allowlist entry allows host") to `boards-api.greenhouse.io` and `api.lever.co`; no provider data observed. |
| 2026-10-06 | Same container after allowing the provider hosts | PASS | `2026-10-06-live-pass.json`: 5/5 boards reached; 235 postings stored per run (run limit 200/source), repeat run stable, all checks passed. Responses captured in `backend/tests/fixtures/jobs/live/`. |
