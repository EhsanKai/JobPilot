# Captured real provider responses

Raw JSON captured from the live Greenhouse Job Board API and Lever Postings
API on 2026-10-06 by `python -m e2e.live_sources --capture tests/fixtures/jobs/live`
(through the production adapters' `JsonFetcher`), used by
`tests/test_live_sources.py` and `tests/test_live_fixture_regressions.py`.

| File | Source endpoint | Postings |
|---|---|---|
| `greenhouse/getyourguide.jobs.json` | `boards-api.greenhouse.io/v1/boards/getyourguide/jobs?content=true` | 54 |
| `greenhouse/hellofresh.jobs.json` | `.../boards/hellofresh/jobs?content=true` | 411 |
| `greenhouse/grover.jobs.json` | `.../boards/grover/jobs?content=true` | 3 |
| `greenhouse/*.board.json` | `.../boards/{token}` (organization name) | — |
| `lever/netlight.json` | `api.lever.co/v0/postings/netlight?mode=json` (all pages) | 35 |
| `lever/ppro.json` | `api.lever.co/v0/postings/ppro?mode=json` | 0 (site exists, no published postings) |

Unmodified except for one redaction: a named individual's contact name and
e-mail address in a Netlight posting were replaced with `Contact Person` /
`contact.person@example.invalid`. Re-capture to refresh; the tests derive
their expectations from whatever is captured.
