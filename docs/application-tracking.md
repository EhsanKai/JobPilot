# Application Tracking (V0.12)

> **The user manages and tracks every application after it exists — by hand, deterministically,
> with a history nobody can rewrite.**

V0.12 turns the V0.7 `Application` into a trackable lifecycle. It is **downstream of V0.11**:
a confirmed submission makes an application *Applied*; everything after that (follow-up,
interview, offer, rejection …) is the user's own record, because JobPilot cannot know what
happens outside the platform.

It deliberately does **not** do anything with e-mail, calendars, reminders, AI or other
automation — see [§11](#11-what-v012-deliberately-does-not-do).

## 1. What existed before V0.12

| Already there | Used by V0.12 as it is |
| --- | --- |
| `Application` (V0.7): one per `UserJob`, composite ownership key, preparation data | the **only** application entity — extended, not duplicated |
| `ApplicationContextSnapshot` (V0.7, immutable) | company, position, source and **original URL** of every tracking row (never the mutable `Job`) |
| `GET /applications` (V0.7), `GET /applications/{id}` | extended with filters / sorting and a `tracking` block — no duplicate endpoints |
| `SubmissionAttempt` (V0.11, append-only audit) | read for the timeline and the submission result; **never copied or changed** |
| `application_status` enum with V0 placeholders + `SUBMITTED` (V0.11) | replaced by the lifecycle below; `SUBMITTED` became `APPLIED` |
| the Application Workspace page, `/applications/{id}` | stays the place to *prepare*; gains a Tracking card |

New in V0.12: the status lifecycle and its state machine, the append-only status history,
`applied_at` / `last_status_change_at` / `follow_up_at`, manual status changes, corrections,
notes, the timeline, **My Applications** (list, filters, sorting), the per-status counts on the
dashboard and profile, and the tracking page `/applications/{id}/tracking`.

## 2. Statuses

`ApplicationStatus` (the Postgres type `application_status`; values equal the names):

| Status | Meaning (what the user sees) |
| --- | --- |
| `SAVED` | the job is known to JobPilot; no application work has started |
| `PREPARING` | the Application Workspace exists and preparation is under way ("In preparation") |
| `APPLIED` | the application was submitted |
| `FOLLOW_UP` | submitted, and the user is waiting or following up |
| `INTERVIEW` | the employer invited the user to an interview |
| `OFFER` | the employer made an offer |
| `REJECTED` | the employer rejected the application |
| `WITHDRAWN` | the user withdrew the application |
| `NO_RESPONSE` | the user considers the application inactive: nobody replied |

`REJECTED` ("they said no") and `NO_RESPONSE` ("they said nothing") are different facts and stay
distinct everywhere: different values, different labels, different symbols and colours.

**Why `SUBMITTED` became `APPLIED`, not a second technical status.** *What happened technically*
(pressed once, confirmation reference, result, unknown result, user verification) already lives
in V0.11's immutable `SubmissionAttempt`. A second "technical" status on the application would
duplicate that and could drift from it. So the application has one user-facing status
(`APPLIED`), and the submission records remain the technical truth.

**New applications start as `SAVED` and become `PREPARING` immediately**, in the same
transaction: *Start Application* is the only way an application comes to exist, and the job
was already in the user's list. Both steps are in the history (SAVED is dated from when the
job was saved, or first discovered). `SAVED` is therefore only a resting state after a manual
correction.

## 3. The state machine

`services/applications/state_machine.py` — a pure table, no database, no clock:

```
SAVED        → PREPARING, WITHDRAWN
PREPARING    → APPLIED, WITHDRAWN
APPLIED      → FOLLOW_UP, INTERVIEW, OFFER, REJECTED, WITHDRAWN, NO_RESPONSE
FOLLOW_UP    → INTERVIEW, OFFER, REJECTED, WITHDRAWN, NO_RESPONSE
INTERVIEW    → OFFER, REJECTED, WITHDRAWN, NO_RESPONSE
OFFER        → WITHDRAWN, REJECTED
REJECTED, WITHDRAWN, NO_RESPONSE → (none: terminal)
```

An ordinary change that is not in the table is refused: **409 `INVALID_STATUS_TRANSITION`**, with
`details: {from, to, allowed}`; nothing is written. A test checks all 72 ordered pairs against a
table written out independently of the code.

**Manual correction.** Real life is messier than any table (a mis-click; a "no response" that
turns into an invitation weeks later). That is allowed — not silently: `correction: true` may
go to any *other* status, **requires a note** saying why, and is recorded as a `CORRECTION`,
distinct from an ordinary change. A correction back to `SAVED` / `PREPARING` clears
`applied_at` (the history still says what happened).

**Same status again is a no-op** (`changed: false`, no history row) — unless it carries a note,
which is recorded as a `NOTE` entry.

## 4. Status history (append-only)

`application_status_history` — one row per event:

| Column | Notes |
| --- | --- |
| `id`, `application_id`, `user_id` | composite FK `(application_id, user_id)` → `applications`: a row can only belong to its own user's application |
| `sequence` | 1, 2, 3 … per application, unique with the application, assigned under the row lock. **Order is the sequence, not the clock** |
| `entry_type` | `STATUS_CHANGE`, `CORRECTION`, `NOTE` |
| `previous_status` | `NULL` only for the first row; for a `NOTE` equal to `new_status` |
| `new_status` | |
| `changed_at` | the server's clock, read after the lock is taken |
| `source` | `SYSTEM`, `USER`, `SUBMISSION` |
| `actor` | `USER` (the authenticated user) or `SYSTEM` — a user-confirmed submission is `source=SUBMISSION, actor=USER` |
| `note` | the user's own words, **exactly as written**; system rows carry none (their text is generated from source and statuses, so nothing JobPilot wrote can pass for something the user said) |
| `submission_attempt_id` | for a submission-caused change: the V0.11 attempt (a reference) |

- **Append-only.** `ImmutableMixin` with no mutable columns: the ORM refuses any change to a
  written row. A later change is another row. (Deleting an application removes its history with it.)
- **Current status vs. history.** `Application.status` is the current status, kept for fast
  queries; the history is the audit trail. They are never reconciled by scanning: **one writer**
  (`tracking._move`) changes both in one transaction, under `SELECT … FOR UPDATE` on the
  application. Invariant, tested after every kind of change, including concurrent ones:
  `Application.status == latest history row's new_status`, sequences are `1..n`, and every
  row's `previous_status` is the row before's `new_status`.
- Database checks back this up: a correction needs a note and `source=USER`; a note keeps the
  status; a `USER` source has a `USER` actor; `uq(application_id, sequence)`.
- **Dates on the application** (no duplicates of what exists): `applied_at` (when it first
  counted as applied; `NOT NULL` for APPLIED / FOLLOW_UP / INTERVIEW / OFFER), `last_status_change_at`
  (a note is not a status change), `follow_up_at`. `created_at` / `updated_at` are V0.7's.

## 5. Submission integration

V0.11's two places that used to set `application.status = SUBMITTED` now call tracking
(`services/applications/tracking.py`); nothing else in the submission code changed.

| V0.11 result | Tracking |
| --- | --- |
| `SUCCESS` (confirmation seen on the page) | `PREPARING → APPLIED`, `source=SUBMISSION`, `actor=SYSTEM`, `changed_at` / `applied_at` = when the result was recorded, `submission_attempt_id` set |
| `SUBMISSION_UNKNOWN` | **no change.** The timeline shows "Submission result uncertain — user verification required." The user decides — either V0.11's *verify* ("it was received" → `APPLIED`, `source=SUBMISSION`, `actor=USER`) or a manual status change |
| `FAILED_BEFORE_SUBMIT` | no change: still preparing |
| user verifies `NOT_SUBMITTED` | no change |

A confirmed submission that arrives when the application is no longer `SAVED` / `PREPARING`
(the user already marked it applied, or moved on) changes nothing and adds no second `APPLIED`; one
that arrives for a `WITHDRAWN` application does not override the user's decision (the attempt is
still on the timeline).

The submission audit is never rewritten: later status changes, corrections and notes touch only
tracking tables (a test compares every `SubmissionAttempt` column before and after). Status
changes cannot start, retry or authorize a submission — there is no path from tracking to
submit, and V0.11's exactly-once protection, consent rules and authorization are unchanged.

## 6. Timeline

`GET /applications/{id}/timeline` — newest first, merging the status history with the V0.11
attempts **read where they live**:

```
kind: STATUS_CHANGE | CORRECTION | NOTE | SUBMISSION
```

A `SUBMISSION` entry carries the attempt's result, destination domain, confirmation reference and
phrase, the user's verification and the failure *code* — never a form value. At the same instant
an attempt sorts before the status change it caused. The UI words each entry in plain
English (`describeTimelineEntry`) and shows the user's note verbatim, as text.

## 7. Follow-up date and notes

- `follow_up_at` is a date-time **the user sets for themselves**. Nothing reads it: no scheduler,
  no reminder, no message — it is displayed ("Follow up on: 21 Oct 2026") and nothing else.
- It is only valid while the user is waiting: APPLIED, FOLLOW_UP, INTERVIEW (422 otherwise;
  the database checks it too). Moving to any other status drops it. A date without an offset is UTC.
  Setting it writes no history row (a reminder is not something that happened).
- A note is user-authored text (≤ 2000 characters), stored exactly (only whitespace-only counts
  as no note), never rewritten, never sent anywhere, and **never given to AI**. Notes on the
  tracking page are separate from the workspace's private `notes` field.

## 8. API

| Method | Path | |
| --- | --- | --- |
| `GET` | `/applications` | **extended**: `status` (repeatable), `company` (contains, any case, `%`/`_` literal), `source`, `applied_from`, `applied_to` (inclusive, UTC), `sort=updated\|applied` (default: most recently updated; `applied` puts unapplied last); items gain `applied_at`, `last_status_change_at`, `follow_up_at`, latest `submission` |
| `GET` | `/applications/summary` | count per status (every status listed) |
| `GET` | `/applications/{id}` | **extended**: `tracking` {status, applied_at, last_status_change_at, follow_up_at, allowed_next_statuses, submission} |
| `PATCH` | `/applications/{id}/status` | `{status, note?, correction?, follow_up_at?}` → `{changed, tracking}` |
| `PATCH` | `/applications/{id}/tracking` | `{follow_up_at?, note?}` (`null` clears the date) → `{changed, tracking}` |
| `GET` | `/applications/{id}/timeline` | `{items}` newest first |

Request bodies are strict (`extra="forbid"`): `previous_status`, `user_id`, `changed_at`, `actor`,
`source`, `sequence`, `application_id` are **rejected** (422), never ignored — the server derives
them. Errors: 404 (not yours / doesn't exist), 409 `INVALID_STATUS_TRANSITION`, 409
`ALREADY_IN_STATUS` (a correction to the current status), 422 (bad status, missing correction note,
follow-up date not applicable, over-long note).

`PATCH /applications/{id}` (the preparation data) still cannot change the status.

## 9. Frontend

- **My Applications** `/applications` (nav item "Applications"): one card per application —
  company — position, status badge, application date, last status change, source, follow-up date,
  submission result, links to the timeline, the workspace and the **original job posting** (opens
  externally; only http(s) URLs are links). Filters: status, company, source, applied from / to;
  sort: last updated / application date; paging; loading, empty (none yet / none match) and
  error (with retry) states.
- **Tracking page** `/applications/{id}/tracking`: header (company, position, status, original
  job link, workspace link), summary (application date, last updated, follow-up date, submission),
  the timeline, and actions — *Change status* (only what the server allows, optional note and
  follow-up date), *Follow-up date*, *Add a note*, *Correct the status* (any status, note required).
- **Workspace** `/applications/{id}`: unchanged, plus a *Tracking* card (status, applied date,
  follow-up, submission result, link to the timeline). The status badge in the header is the same
  component.
- **Dashboard** and **Profile**: a compact count (Applied, Follow-up, Interview, Offer, Rejected,
  No response — only those with applications) and a *My Applications* link. Counts only; no analytics.
- **Status visuals** (`StatusBadge`, `STATUS_PRESENTATION`): five tones — active, positive
  (interview, offer), rejected, withdrawn, no response — each with a **text label and a symbol**
  (decorative, `aria-hidden`) as well as a colour; the meaning is also the tooltip. Never colour alone.

## 10. Privacy, ownership, concurrency

- Every query is scoped by `Application.user_id == user.id`; another user's application is the
  existing 404 (list, detail, status change, tracking update, timeline, summary — all tested at the API
  level, and again in the browser). No user id is ever taken from a request.
- **Concurrency.** A status change locks the application row, reads the current status and
  sequence under the lock, validates against *that*, and writes status and history together. Two
  racing changes are both applied in the order the lock grants, the second validated against what
  the first left behind (an identical racing change is a no-op); racing notes are all kept in
  order. Tested with real concurrent database sessions.
- A change in the workspace (`PATCH /applications/{id}`) takes the same row lock.
- The application is never deleted because its posting disappeared: company, position, source and
  URL come from the V0.7 snapshot, and nothing in tracking reads the mutable `Job`.

## 11. What V0.12 deliberately does not do

No AI follow-up e-mails, automatic follow-ups or reminders that send anything, interview
preparation, offer negotiation, rejection analysis, campaigns, **automatic status inference** (from
e-mail, text or timing — the status moves only because the user said so or V0.11 confirmed a
submission), Gmail / Outlook / calendar / LinkedIn integration, background scheduler, analytics
dashboards, success-rate AI, or bulk actions. These belong to later versions and can build on
this foundation: a source of events can write `SYSTEM`-sourced history rows through the same
single writer, behind the same state machine.

## 12. Migration `d6a3f1b8c2e5`

- `application_status` is **replaced** (Postgres cannot drop or rename enum values): old type
  renamed aside, new type created, column converted with an explicit mapping, old type dropped.
  `DRAFT → SAVED`, `IN_PROGRESS → PREPARING`, `PREPARING → PREPARING`, `SUBMITTED → APPLIED`,
  `INTERVIEWING → INTERVIEW`, `OFFER` / `REJECTED` / `WITHDRAWN` unchanged. (V0–V0.11 could only
  produce `PREPARING` and `SUBMITTED`.)
- New columns `applied_at`, `last_status_change_at` (backfilled from `updated_at`), `follow_up_at`;
  new table `application_status_history` and three enum types; check constraints and a
  `(user_id, status)` index.
- **Every existing application gets the history it would have had** (SAVED at the time the job
  was saved or discovered, PREPARING at creation, then its current status — `APPLIED` rows from
  `SUBMISSION`, with the confirmation time and attempt of their V0.11 `SUCCESS` or the user's
  verification), so `status == latest history row` holds for existing data too.
  `automation_*` tables are untouched byte for byte (tested).
- `downgrade()` drops the history and new columns/constraints and converts back:
  `APPLIED`, `FOLLOW_UP` → `SUBMITTED`; `INTERVIEW` → `INTERVIEWING`; `SAVED` → `DRAFT`;
  `NO_RESPONSE` → `WITHDRAWN` (a closed application — `REJECTED` would claim something the employer
  never said). Every application survives; the history and follow-up dates are lost.
- Tested with realistic V0.11 data (a confirmed submission, a preparing application, a legacy
  `INTERVIEWING` row): upgrade → downgrade → upgrade, schema equal to the models
  (`alembic check`), and the lossy downgrade mapping.

## 13. Known limitations

- Only a *confirmed* result moves an application to `APPLIED` automatically. If the process dies
  between V0.11 recording `SUCCESS` and tracking applying it (two commits), the attempt shows
  `SUCCESS` while the status is still `PREPARING` until the user sets it (or verifies); the
  timeline shows the submission either way. (Closing that window would mean changing V0.11's
  commit structure, which V0.12 does not do.)
- Setting a follow-up date is not itself a history event.
- A date range filters on the *application date* (`applied_at`); applications not applied yet have none.
- No bulk operations and no archive/delete of an application (as in V0.7).
