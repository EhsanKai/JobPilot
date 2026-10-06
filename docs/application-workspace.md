# V0.7 — Application Workspace & Application Preparation Foundation

V0.7 answers *"I want to apply to this job — where do I prepare?"* It adds the
user's private, persistent **Application Workspace**: the place later versions
(tailored CV, Anschreiben, application-question answers, browser automation,
review and tracking) will read from and write to.

```
Job Details ──► Start Application ──► Application created ──► Application Workspace
                (explicit click)       one per job            job · salary expectation · availability ·
                                                              notice period · language · resume · notes
```

**A job match is not an application.** A user can discover, match, save and
inspect a job without ever applying. An application exists only after the user
explicitly chooses **Start Application** — and even then nothing is submitted
or sent to anyone: starting an application only creates the user's own
preparation workspace.

V0.7 is deliberately *only* the foundation. It contains no CV or cover-letter
generation, no AI, no browser automation, no auto-fill or submission, and no
new matching logic (see [§17](#17-scope-and-whats-deferred)). V0.8 builds on it:
the tailored CV and Anschreiben are generated from an application's context
snapshot, resume reference and preparation data (see
[`tailored-documents.md`](tailored-documents.md)). V0.9 adds the employer's
**application questions** to the workspace, with suggested answers the user
controls, plus Humanize / edit / photo for the documents (see
[`application-questions.md`](application-questions.md)).

**Workspace sections** (the in-page navigation of `/applications/[id]`):

```
Application
├── Job · Match                    what you are applying to, and how well it fits
├── Preparation                    salary, availability, notice period, language, resume
├── CV · Anschreiben               generated, editable, copyable, versioned        (V0.8 / V0.9)
├── Questions                      the employer's questions and your answers       (V0.9)
└── Notes                          private
```

**Human Control at Every Stage** is a core principle of the workspace: the user owns
the application and can complete it without AI; AI suggestions are never authoritative
over user-authored content.

## Contents

1. [Concepts: Job, UserJob, MatchResult, Application](#1-concepts)
2. [Ownership model](#2-ownership-model)
3. [Data model](#3-data-model)
4. [Application data vs. user profile](#4-application-data-vs-user-profile)
5. [Status](#5-status)
6. [One application per UserJob](#6-one-application-per-userjob)
7. [Historical context (the snapshot)](#7-historical-context-the-snapshot)
8. [Source posting and original URL](#8-source-posting-and-original-url)
9. [Resume reference](#9-resume-reference)
10. [Preparation fields and validation](#10-preparation-fields-and-validation)
11. [Preparation progress](#11-preparation-progress)
12. [Match integration](#12-match-integration)
13. [API](#13-api)
14. [Frontend](#14-frontend)
15. [Database and migration](#15-database-and-migration)
16. [Security](#16-security)
17. [Scope and what's deferred](#17-scope-and-whats-deferred)
18. [Limitations](#18-limitations)

## 1. Concepts

```
Job                          the vacancy (global, canonical; V0.3/V0.4)
 └── UserJob                 what *this user* did with it: discovered, saved   (user_jobs)
       ├── MatchResult       how well it fits *this user*                      (job_matches, V0.6)
       └── Application       the user's preparation workspace for it           (applications, V0.7)
             ├── ApplicationContextSnapshot   what the job was when the user started
             ├── PreparationData              salary, availability, notice period, language, notes ...
             └── ResumeReference              which resume version it builds on
```

| Concept | Scope | Changes when... |
| --- | --- | --- |
| **Job** | global, one per real-world vacancy | discovery refreshes it |
| **UserJob** | one per (user, job) | the user's discovery runs return it, or they save it |
| **MatchResult** | per user and job, versioned | the user explicitly calculates / recalculates |
| **Application** | exactly one per UserJob | the user edits their preparation data |
| **ApplicationContextSnapshot** | one per application, **write-once** | never |

The four are separate on purpose. Matching is not application, the canonical
job is not the user's private data, and the user's preparation data is not
part of the job.

## 2. Ownership model

```
current_user ──owns──► UserJob ──owns──► Application
```

An application belongs to exactly one `UserJob` and therefore to the user who
owns it. This is enforced at every level:

- **API.** Every application query is scoped by the authenticated user. Another
  user's application — and one that doesn't exist — are the same `404`, with
  the same message, so nothing can be inferred. The same goes for starting an
  application on a job that isn't in the user's own job list (visibility is the
  V0.3 rule: no `UserJob` row → 404).
- **Database.** `applications.user_id` is denormalized for cheap filtering, and
  a composite foreign key `(user_job_id, user_id) → user_jobs (id, user_id)`
  makes it impossible to store an application for someone else's `UserJob`.
  This is the same technique V0.5 uses for `job_matches`.
- **No client-controlled ownership.** Request bodies reject unknown fields, so a
  client cannot send `user_id`, `job_id`, `user_job_id` or `status`.

## 3. Data model

### 3.1 `applications` (evolved from the V0 placeholder)

V0 created an `applications` table as a schema-only foundation; no code ever
wrote to it. It was **evolved in place** (like `jobs` in V0.3) rather than
replaced, so no concept exists twice:

| Column | Notes |
| --- | --- |
| `id` | |
| `user_id` | V0 column, kept; denormalized owner (see §2) |
| `user_job_id` | **new**, NOT NULL, **UNIQUE**; the application's UserJob |
| `job_id` | V0 column, kept; mirrors the UserJob's job so `Job.applications` keeps working. The UserJob is authoritative |
| `status` | V0 column; V0.7 only creates `PREPARING` (§5) |
| `source_posting_id` | **new**; the source posting chosen as the application target; `ON DELETE SET NULL` |
| `resume_id` | **new**; the resume version it builds on; `ON DELETE SET NULL` (§9) |
| `salary_expectation`, `salary_currency` | V0 columns, reused as the salary expectation |
| `mention_salary_in_letter` | **new**; nullable boolean — `NULL` = not decided |
| `available_from` | V0's `start_date`, **renamed**: the date the candidate can start |
| `notice_period_value`, `notice_period_unit` | **new**; days / weeks / months |
| `work_authorization` | **new**; optional; `authorized` / `requires_sponsorship` / `unknown` |
| `application_language` | **new**; ISO 639-1 key (`de`, `en`); `NULL` until the user chooses |
| `notes` | **new**; private |
| `created_at`, `updated_at` | V0 columns |

Constraints and indexes: `uq_applications_user_job_id` (unique),
`fk_applications_user_job` (composite, `ON DELETE CASCADE`), checks for a
non-negative salary, a consistent notice period (both parts or neither, never
negative) and a two-letter language key, and indexes for listing
(`user_id, updated_at`) and for the `SET NULL` foreign keys.

### 3.2 `application_context_snapshots`

One write-once row per application: `application_id` (unique, `ON DELETE
CASCADE`), `schema_version`, `content_hash` and the validated `document` (JSONB).
Details in §7.

### 3.3 Enums

- `application_status` gains `PREPARING` (the V0 values stay).
- `notice_period_unit`: `DAYS`, `WEEKS`, `MONTHS`.
- `work_authorization`: `AUTHORIZED`, `REQUIRES_SPONSORSHIP`, `UNKNOWN`.

The application language is a plain two-letter key rather than an enum: which
languages are offered is application configuration
(`SUPPORTED_APPLICATION_LANGUAGES`, currently `de` and `en`), so adding French
later needs no migration.

## 4. Application data vs. user profile

```
User Profile      persistent facts and preferences    salary preference 70,000–80,000
                                                      desired title, work model ...
Application       specific to THIS application        salary expectation for this job: 78,000
                                                      mention salary in the letter: yes
                                                      available from 2027-01-01 · notice 3 months
                                                      application language: German
```

Changing an application never writes to the profile, preferences, the job, the
source postings, the user's `UserJob` or match results — and none of those
writes to an application. A test snapshots all of them (including their
`updated_at`) before and after a full update to prove it.

The salary expectation uses the same convention as Job Preferences: annual
gross, a `NUMERIC(12,2)` amount plus a three-letter currency, never free text.
Nothing is prefilled from the profile or the job: the user states it.

## 5. Status

V0.7 needs one status. `PREPARING` is added to the existing
`application_status` type and is the only value V0.7 ever sets.

The V0 values (`DRAFT`, `IN_PROGRESS`, `SUBMITTED`, `INTERVIEWING`, `OFFER`,
`REJECTED`, `WITHDRAWN`) were placeholders for the complete lifecycle, which
belongs to V0.12. Postgres cannot drop enum values and existing rows may use
them, so they are kept untouched and still readable — but nothing in V0.7 sets
them, and `PATCH` cannot change the status. V0.7 adds no `INTERVIEW`, `OFFER`,
`FOLLOW_UP` or `NO_RESPONSE`.

## 6. One application per UserJob

`unique(user_job_id)` makes the database refuse a second application for the
same user and job. The service follows it:

```
POST /jobs/{id}/application
  application exists?  ──yes──► 200, the existing application (the body is ignored)
        │ no
        ▼
  create application + context snapshot in one savepoint
        │
   unique violation (another request won the race)?  ──► 200, the winner's application
        │ no
        ▼
       201
```

Starting is therefore idempotent: a double click, a retry, a second tab — all
open the same application. The guarantee is the database's, not the frontend's:
a test starts eight concurrent requests (separate sessions, a barrier) and
asserts one application, one snapshot and exactly one `created`; with the
unique constraint removed that same test produces several rows.

Starting an application that exists never changes it: preparation fields in the
body are used only when creating.

## 7. Historical context (the snapshot)

The canonical `Job` changes: companies edit postings, and every discovery run
can refresh it. The application keeps what the job *was* when the user started
it, so later generation (CV, Anschreiben, question answers) can reproduce which
job the user was applying to.

```
{
  "schema_version": "application_context_v1",
  "origin": "created",                    // or "migrated" (§15)
  "captured_at": "...",
  "job":    { job_id, canonical_url, apply_url,
              title, company_name, location, locations, country, department,
              description, requirements, responsibilities, technologies,
              salary_min/max/currency/period/text,
              employment_type, work_model, seniority, industry, posted_at },
  "source": { posting_id, source, source_job_id, source_company_key,
              title, company_name, location, posted_at, source_updated_at,
              original_url, canonical_url, apply_url, application_url,
              was_preferred }
}
```

- **Reuses the existing mechanism.** The `job` part has exactly the fields of
  V0.4's `CANONICAL_CONTENT_FIELDS` (the columns a posting's content becomes on
  `Job`), built with the same `content_of_job` / `encode_content` helpers, plus
  the canonical job id and URLs. It is not a copy of the database object.
- **Write-once.** The model uses V0.5's `ImmutableMixin`: the ORM refuses any
  change to a stored snapshot. Refreshing, merging or deleting the live job and
  its postings never changes it (tested by rewriting the job's title, salary,
  description, URL and company after the application started).
- **A separate table, not a column**, for the same reason V0.5 stores candidate
  snapshots separately: an immutable record next to a mutable one is clearer and
  can be enforced as a whole. It also leaves room for more than one snapshot per
  application later.
- **Checksummed.** `content_hash` is the SHA-256 of the canonical JSON that is
  stored, so a generated artifact can pin exactly which context it was built
  from, and a snapshot can be verified at any time.
- **Nothing the user wrote.** Every document part forbids unknown keys, so notes,
  salary expectation, resume reference and raw source payloads cannot appear in
  it.

## 8. Source posting and original URL

V0.4 merges the same vacancy from several sources into one canonical job, each
with its own posting and URL. The application remembers which posting it was
started through:

- by default the job's **preferred posting** (`preferred_source_posting_id`), or
  another posting of the same job if the client asks (`source_posting_id`; a
  posting that isn't the job's is rejected with `422`);
- `applications.source_posting_id` references the posting; the snapshot keeps
  its identity and URLs, so they survive the posting row disappearing.

The workspace exposes company, job title, location and source, and **View
original posting** (`context.source.original_url`: the URL exactly as the source
returned it). Where applying actually happens is `context.source.application_url`
and reuses V0.4's rule: the posting's explicit apply URL, else the posting page
itself for sources that host the form (Greenhouse, Lever). For any other source
it is `null` — the job's canonical URL is **never assumed** to be the
application URL.

`source_is_active` is live (not snapshotted): when the source no longer lists
the posting the workspace says so.

## 9. Resume reference

A `Resume` row *is* a resume version (V0.2 versions uploads per user), so the
application references it with `resume_id` — a reference, never a copy of
contents.

- **Default** (when the request names none): the resume of the user's most
  recently *confirmed* Resume Profile — the same one matching uses — else their
  latest upload (it may still need review; the workspace says "Not confirmed
  yet"), else none.
- **Stable.** Uploading a newer resume creates a new version and leaves existing
  applications on the version they started with. Re-sending the current resume
  id is a no-op.
- **Explicit changes only.** `PATCH` can replace the resume with another of the
  user's own; it cannot clear it. A resume that isn't the user's — or doesn't
  exist — is a `422` with the same message either way.
- **Deleting a resume still works** (users must be able to delete their data):
  the reference becomes `NULL`, the application and everything else stay, and the
  checklist asks for a resume again.

V0.7 does not generate a tailored CV; V0.8 will build it from this reference.

## 10. Preparation fields and validation

| Field | Rule |
| --- | --- |
| `salary_expectation` | decimal, `> 0`, `≤ 10,000,000`, at most 2 decimal places; annual gross |
| `salary_currency` | 3 letters, upper-cased (`eur` → `EUR`); **required together with a salary** (checked on the merged state, so a later update may send just the amount) |
| `mention_salary_in_letter` | `true` / `false` / `null`. `null` = not decided; no default is chosen for the user |
| `available_from` | a date from 2000 to at most 5 years ahead. A date in the past means "available now" (so an old value never makes a later save fail) |
| `notice_period` | `{value, unit}`; both or neither; `0` = immediately; at most 730 days / 104 weeks / 24 months. No legal assumptions |
| `work_authorization` | optional; `authorized` / `requires_sponsorship` / `unknown` |
| `application_language` | `de` or `en`; `null` until chosen — never guessed from the job |
| `notes` | up to 10,000 characters, trimmed; blank means none |

Updates are partial: an omitted field is untouched, an explicit `null` clears
it. An empty body changes nothing (and doesn't touch `updated_at`). Unknown
fields — `status`, `user_id`, `job_id`, `source_posting_id`, `context` ... — are
rejected with `422`.

**Notes are private.** They are the user's own; JobPilot never sends them to an
employer, and they are not part of the context snapshot.

## 11. Preparation progress

A deterministic checklist computed from the data; nothing is stored, so it can
never disagree with it, and there is no workflow engine:

```
✓ Job selected                       always
✓ Resume selected                    a resume version is referenced
○ Application language selected
○ Salary expectation                 amount and currency
○ Availability (start date)
○ Notice period
○ Work authorization (optional)      shown, never counted
```

`progress = { completed, total, items: [{ key, complete, required }] }`; the
frontend maps keys to labels. Later versions add their own steps when they
exist.

## 12. Match integration

The workspace shows the user's current match summary — Overall, Job and
Preference Match, the recommendation, and whether it is out of date — with a link
to the full explanation on the job page. It is read through the same code as the
job list, never recalculated: creating, reading and updating an application never
adds or changes a `job_matches` row (tested). Application preparation is a
separate domain from matching.

## 13. API

All endpoints require authentication and are scoped to the current user.

```
POST   /jobs/{job_id}/application      Start Application: 201 + the new application,
                                       or 200 + the existing one. Body optional.
GET    /applications                   your applications, most recently updated first
                                       (page, page_size ≤ 100)
GET    /applications/{id}              the full application
PATCH  /applications/{id}              update application-specific data
```

`POST /jobs/{job_id}/application` body (all optional; used only when creating):
the preparation fields above, `resume_id` and `source_posting_id`.

`GET /applications/{id}` returns the application metadata and status, the
preparation data, `context` (the snapshot with its checksum), `source_is_active`,
`resume` (`id`, `version_number`, `file_name`, `status`, `is_confirmed`),
`match` and `progress`. List items are compact: title, company, location,
source, original URL, status and progress.

`GET /jobs` and `GET /jobs/{id}` include `application: { id, status } | null`, so
the job page can offer **Start** or **Continue**.

Errors use the standard envelope. `404` for anything not yours; `422` for
validation (the message is user-facing, `details.field` or `details.errors[].loc`
names the field).

There is no `DELETE`: V0.7 has no way to discard a workspace, and the lifecycle
(withdrawn / archived) belongs to V0.12.

## 14. Frontend

- **Job details** has an *Application* card: **Start Application** (a button;
  creates or reopens the workspace and navigates to it) or **Continue
  Application** (a link) with the status. It explains that nothing is submitted.
  Opening, saving or matching a job never starts an application. Job cards show an
  "Application: In preparation" chip linking to the workspace.
- **`/applications/[id]`** is the workspace: the job as it was captured (title,
  company, location, source, salary as posted, **View original posting** and, when
  different, the **Application page**), the preparation form, the resume (with a
  version choice when there is one), private notes, the progress checklist and the
  match summary. One form saves one `PATCH` containing only the fields that
  changed; validation errors appear next to their field.

Code: `frontend/components/applications/` (`ApplicationCard`,
`ApplicationWorkspace`), `frontend/lib/applications.ts` (form model, change
detection, validation, error mapping), `frontend/lib/api/applications.ts`,
`frontend/types/application.ts`.

## 15. Database and migration

Migration `e8b3c1a5d7f2` (`v0_7_application_workspace`), additive:

- adds the new columns, the `PREPARING` value, two enum types, the unique
  constraint on `user_jobs (id, user_id)` (the composite key's target), the
  constraints and indexes above, and `application_context_snapshots`;
- renames `start_date` → `available_from` (the same concept); no other column,
  index or row is changed or dropped.

**Existing (V0) applications** are attached to the `UserJob` of their user and
job (created when the user never had one, with no discovery or save timestamps)
and get a snapshot rebuilt from the job as it is when the migration runs
(`origin: "migrated"` — the earliest state still available). The status of an
existing row is never rewritten. V0–V0.6 had no way to create applications, so
none are expected; rows that cannot belong to exactly one UserJob — no job, or
several applications of one user for the same job, or a negative salary — stop
the migration with an explanatory error before anything changes, rather than
being merged or deleted.

**Downgrade** removes everything V0.7 added and restores the V0.6 schema exactly
(a test compares columns, constraints, indexes and enum values). V0.7-only data
(notes, language, notice period, snapshots ...) is lost, and `PREPARING` rows
become `DRAFT` (Postgres cannot drop an enum value) — the rows themselves are
kept. Upgrade → downgrade → upgrade is tested with data in every table.

Deleting a user removes their applications; deleting a job removes the
applications for it; deleting a resume or a posting sets the reference to `NULL`.

## 16. Security

- Authentication on every endpoint; ownership as in §2; cross-user access is
  indistinguishable from "not found".
- No client-controlled ownership or status (strict request bodies).
- A resume or posting reference is validated: the resume must be the user's,
  the posting the job's.
- Notes and preparation data never leave the user's own responses; they are not
  part of any document that is shared or sent (nothing is sent in V0.7).
- Tests cover: user B can't read, update or list user A's application, can't
  start one on A's job, and can't smuggle an ownership reference past the
  database (composite foreign key).

## 17. Scope and what's deferred

V0.7 **does not** include, and nothing in it accidentally implements:

| Deferred to | Feature |
| --- | --- |
| V0.8 | tailored CV generation; Anschreiben (German / English) |
| V0.9 | AI answers to application questions |
| V0.10 | browser automation, auto-fill |
| V0.11 | review and submission |
| V0.12 | the full application lifecycle and tracking (interviews, offers, follow-ups, no-response); an applications list UI |

It also makes no AI calls, changes no scraping, adds no matching or scoring
logic, no taxonomy vocabulary and no job-board integration. It is domain-neutral:
nothing refers to one profession, industry, language or board.

## 18. Limitations

- Salary is annual gross only (the Job Preferences convention); a single amount,
  not a range.
- Only German and English can be selected as the application language.
- The target posting is chosen when the application starts; re-targeting is not
  supported yet.
- Work authorization is a single statement about this application, not a country
  list.
- There is no applications list page (the `GET /applications` API exists, and job
  cards link to their application) and no way to delete an application.
- The context of an application migrated from a V0 row is the job as of the
  migration, not as of the (unrecorded) original start.
