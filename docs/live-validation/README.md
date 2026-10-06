# Live source validation reports

Evidence from `python -m e2e.live_sources` (see `docs/job-intelligence.md` §22).
Each report is the harness's raw JSON output: probes (reachability, HTTP
status, response shape), discovery summaries and analysis when a provider
was reachable. Reports are never edited by hand.

| Date | Environment | Status | Notes |
|---|---|---|---|
| 2026-10-06 | Claude Code cloud container (V0.4.1 development) | BLOCKED | Egress proxy denied `CONNECT` (403) to `boards-api.greenhouse.io` and `api.lever.co`; no provider data observed. |
