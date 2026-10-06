"""Application Workspace & Application Preparation Foundation (V0.7).

    context.py      canonical Job + selected source posting -> immutable context document
    progress.py     the deterministic preparation checklist
    refs.py         "your application for this job" lookups (embedded in job list / details)
    service.py      start (idempotent), read, update and list -- always through the user's UserJob

An Application belongs to exactly one UserJob and so to exactly one user;
every query here is scoped to the requesting user, and another user's
application is a 404, exactly like one that doesn't exist. Starting an
application is explicit; nothing else creates one. V0.7 prepares the
workspace only: no document generation, form filling or submission (V0.8+).
See docs/application-workspace.md.

This module deliberately imports nothing: `job_service` imports `refs`, and
`service` imports `job_service`.
"""
