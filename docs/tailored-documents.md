# V0.8 — Tailored CV & Anschreiben Generation

V0.8 answers *"Write my application documents for this job — without making
anything up."* For an Application (V0.7), JobPilot can generate a **tailored
CV** and an **Anschreiben** (cover letter) from the user's *confirmed* resume,
the job as it was when the application started, and the application's own
preparation data. Both are written in the application's language, and neither
is trusted merely because an AI returned text: every document is checked
against the resume before it exists.

```
Application
 │
 ├── ApplicationContextSnapshot      the job, as captured when the application started   (V0.7)
 ├── PreparationData                 language, salary decision, availability ...          (V0.7)
 ├── ResumeReference                 the resume version it builds on                      (V0.7)
 │
 └── GeneratedDocuments                                                                   (V0.8)
       ├── TailoredCV     v1, v2, v3 ...   (each: validated, with provenance)
       └── CoverLetter    v1, v2 ...
```

```
Application ─► deterministic generation context ─► AI (structured output) ─► truth validation ─► validated document
                  facts · job snapshot · prep data      AIProvider                 rejects invented              numbered version,
                                                                                   claims, never repairs         becomes current
```

## Contents

1. [Truth first](#1-truth-first)
2. [The data model](#2-the-data-model)
3. [Lifecycle](#3-lifecycle)
4. [The generation context](#4-the-generation-context)
5. [Prompt versions](#5-prompt-versions)
6. [Structured AI output](#6-structured-ai-output)
7. [Tailored CV rules](#7-tailored-cv-rules)
8. [Anschreiben rules](#8-anschreiben-rules)
9. [Salary](#9-salary)
10. [Truth validation](#10-truth-validation)
11. [Provenance](#11-provenance)
12. [Regeneration and versions](#12-regeneration-and-versions)
13. [AI originals and user edits](#13-ai-originals-and-user-edits)
14. [Failure handling](#14-failure-handling)
15. [API](#15-api)
16. [Authorization](#16-authorization)
17. [Frontend](#17-frontend)
18. [Database and migration](#18-database-and-migration)
19. [Testing](#19-testing)
20. [Limitations](#20-limitations)
21. [Scope and what's deferred](#21-scope-and-whats-deferred)

## 1. Truth first

The AI must never invent a candidate fact. V0.8 enforces that in three layers,
none of which relies on the model behaving:

1. **The AI cannot write plain facts.** It writes only a summary, the wording of
   experience bullets, the order and emphasis of skills, and the body
   paragraphs of a letter. Employers, job titles, locations, dates, degrees,
   skill names, languages, certifications, projects and contact details are
   *copied by the server* from the confirmed resume
   (`services/documents/assembly.py`); the output schema has no field for
   them. A skill is chosen by id, so it cannot be renamed or added.
2. **Everything the AI writes must cite its facts.** Each bullet, summary and
   paragraph carries `source_fact_ids`. A bullet may cite only facts of the role
   it describes; an unknown id, a missing citation or a fact of another role is
   a violation.
3. **The text is checked against the facts it cites** (§10): numbers, skills,
   languages and degrees, tools and acronyms, organizations, leadership and
   seniority claims, certifications, achievements, claims about the company or
   the writer's life, and any mention of pay. Wording may change freely; claims
   may not.

A document that fails any check is **rejected whole** — the system never
"fixes" an unsupported claim, because a repair would be a guess. Only a
validated result is a document.

Allowed: rephrasing, summarizing, re-ordering, combining supported facts, and
translating into the application language. Not allowed: better wording that
becomes invented experience.

## 2. The data model

One table, `generated_documents`, holds **every generation attempt**
(`GeneratedDocument`, `app/models/generated_document.py`). Regeneration adds a
row; no row is overwritten.

| Column | Notes |
| --- | --- |
| `application_id`, `user_id` | Composite FK `(application_id, user_id) → applications (id, user_id)`: a document cannot be stored for someone else's application |
| `document_type` | enum `TAILORED_CV` \| `COVER_LETTER` |
| `status` | enum `GENERATING` \| `VALIDATED` \| `REJECTED` \| `FAILED` (§3) |
| `version_number` | 1, 2, 3 … among the **validated** documents of this application and type; `NULL` otherwise. Unique per `(application_id, document_type, version_number)` |
| `language` | the application language the document is written in |
| `resume_id`, `resume_profile_id`, `resume_version_number` | which resume version and confirmed profile the facts came from (`SET NULL` when the resume is deleted; the version number survives) |
| `application_context_snapshot_id` | the immutable job context it was generated for |
| `input_hash` | SHA-256 of the exact generation input (§4) |
| `prompt_version`, `ai_provider`, `ai_model` | provenance (§11) |
| `content` | the validated, assembled document (JSONB, `TailoredCvContent` / `CoverLetterContent`); only on `VALIDATED` |
| `source_facts` | `{fact id: {text, ref}}` for every fact the AI was shown, so each citation stays resolvable after the resume changes |
| `validation` | the `ValidationReport` (JSONB): why it passed or was rejected |
| `error_code`, `error_message` | user-safe description of a failed / rejected attempt |
| `created_at`, `updated_at`, `completed_at` | |

Constraints the database enforces:

- `ck_generated_documents_version_iff_validated` and
  `ck_generated_documents_content_iff_validated`: **only a `VALIDATED` attempt
  has a version and content**, and a validated one must have both. A failed or
  rejected attempt can never look like a document, whatever the application
  code does.
- `uq_generated_documents_version`: a version number is used once per
  application and type.
- `uq_generated_documents_one_in_flight`: a partial unique index allows one
  `GENERATING` attempt per application and type — a second request while one
  runs is refused by the database, not by a race.
- `ck_generated_documents_version_number` (≥ 1) and `ck_generated_documents_language`.

The model also makes a **finished attempt write-once**: once `VALIDATED`,
`REJECTED` or `FAILED`, the ORM raises `ImmutableRecordError` for any change
(`ImmutableMixin`'s idea, adapted to a row that must change while it runs).
The AI original and its provenance cannot be altered by application code.

"Current" is **derived, not stored**: the highest-numbered validated document
of a type. It cannot disagree with the data.

## 3. Lifecycle

```
GENERATING ──AI call failed / unusable output────────► FAILED
GENERATING ──truth validation found unsupported claims─► REJECTED
GENERATING ──validation passed────────────────────────► VALIDATED   (next version number; current)
```

The recommended lifecycle (`REQUESTED → GENERATING → GENERATED → VALIDATED /
REJECTED / FAILED`) was collapsed deliberately: the whole pipeline runs inside
one request, so *requested* and *generated-but-not-yet-validated* are never
observable states. Unvalidated AI output is never written to the database at
all — the AI's text exists only in memory until validation passes.

Nothing is stuck "generating":

- every exit from the pipeline — provider error, malformed output, rejection,
  unexpected exception, request cancellation — moves the attempt to a terminal
  state;
- an attempt still `GENERATING` after `DOCUMENT_GENERATION_STALE_MINUTES`
  (default 10; e.g. a server restart mid-request) is *shown* as `failed`
  (`GENERATION_ABANDONED`) on every read, marked failed for good by the next
  generation, and never blocks it;
- the UI follows a generation running elsewhere with bounded polling.

## 4. The generation context

`services/documents/context.py` builds the context from **named inputs**, never
"the database":

| Input | Source |
| --- | --- |
| candidate facts | the **confirmed** Resume Profile of the application's resume: accepted and edited items only — nothing pending, nothing rejected |
| job | the application's context snapshot (title, company, location, responsibilities, requirements, technologies, work model, ...) — what the user saw, not the live job |
| application | language; for a letter also availability, notice period, work authorization |
| match (letter only) | the *current* V0.6 result as hints (strong matches / gaps), only when up to date; never recalculated |

`build_generation_context` is a pure function: same inputs, same prompt payload,
same fact ids, same `input_hash`. It is unit-tested for determinism.

**Fact ids** follow the profile's order and are short and readable:
`summary`, `exp1`, `exp1.r1` (responsibility), `exp1.a1` (achievement),
`exp1.d` (description), `exp1.t` (technologies), `edu1`, `skill3`, `lang2`,
`cert1`, `proj1`, `add1`, `derived.years`, and for a letter `app.available_from`,
`app.notice_period`, `app.work_authorization`. `derived.years` is the total
experience computed from the resume's dates (V0.2's `compute_derived`), only
when every role has usable dates and it is at least a year.

**What the AI is not shown:** contact details (copied by the server), notes, the
salary figure, other applications, other resumes, the profile, preferences, or
anything else. Job lists are bounded (15 responsibilities and requirements of
300 characters each, 30 technologies, a 3,000-character description excerpt only
when the posting has no lists).

**`input_hash`** covers the payload *and* the server-side parts of the final
document (header, subject, salary sentence, ...). It is stored with the
document. When the hash of today's context differs, the document is
**outdated** — the resume, the application data or the match changed — and the
UI says so. (A CV does not depend on availability; a letter does.)

## 5. Prompt versions

| Type | Version | File |
| --- | --- | --- |
| Tailored CV | `tailored_cv_v1` | `app/ai/prompts/tailored_cv_v1.txt` |
| Cover letter | `cover_letter_v1` | `app/ai/prompts/cover_letter_v1.txt` |

The version is stored with every document. The prompt text is not stored in the
database and never appears in an API response. Released prompts are never
edited in place: a behavioral change gets a new file and a new version, and
`PROMPT_FINGERPRINTS` (SHA-256 of each released file, checked by a test) makes
a silent edit fail loudly.

The prompts treat the context as data (a prompt-injection defence for resume
and job text — validation protects the output regardless), forbid every
invention listed in §1, and tell the model to leave out anything the facts do
not support: *a shorter truthful CV is correct*.

## 6. Structured AI output

Never a raw text blob. The provider is asked for a Pydantic schema
(`app/schemas/document_ai.py`) through the existing `AIProvider` abstraction.

```jsonc
// AITailoredCV
{ "summary":    { "text": "...", "source_fact_ids": ["summary", "derived.years"] },
  "experience": [ { "source_experience_id": "exp2",
                    "bullets": [ { "text": "...", "source_fact_ids": ["exp2.r1"] } ] }, ... ],
  "skills":     [ { "source_fact_id": "skill4", "emphasized": true }, ... ] }

// AICoverLetter
{ "language": "de",
  "paragraphs": [ { "role": "opening|role_fit|experience|skills|motivation|closing",
                    "text": "...", "source_fact_ids": ["exp1", "exp1.r1"], "job_fact_ids": ["job.resp1"] } ] }
```

Limits (also in the schema): summary 900 characters, bullet 500 (max 8 per
role), at most 40 skills, paragraph 1,600 (max 8), 12 fact ids per text.
Output beyond them is invalid output, never silently truncated.

The stored document is the **assembled** result (`TailoredCvContent`,
`CoverLetterContent` in `app/schemas/document_content.py`): the AI's text, the
resume's plain facts, and the fixed wording of a letter — with the
`source_fact_ids` kept next to each piece of AI text.

## 7. Tailored CV rules

The tailored CV remains the user's CV, not a new fictional one.

- **Summary** — rewritten to emphasize the experience relevant to the target
  job, citing the facts it rests on.
- **Experience** — every role appears exactly once, with its employer, title,
  location and dates copied from the resume. Roles may be **re-ordered** by
  relevance (dates are never changed). Bullets are **rewritten** for clarity,
  relevance and terminology, using only facts of that role.
- **Skills** — chosen by id from the resume's own skills, ordered by relevance,
  with `emphasized` for the ones the job asks for. Leaving a skill out is
  allowed; **adding** one is impossible. A job that asks for AWS never puts AWS
  on a CV whose resume lacks it, and an AWS claim in a bullet is rejected.
- **Education, languages, certifications, projects, additional information** —
  copied verbatim, in resume order.
- **Header** — name, email, phone, location and links from the resume's personal
  information.
- The CV never mentions salary, notes, the job's company or the job's title as
  a claim about the candidate.

## 8. Anschreiben rules

- **Language** is the application's language (`de` or `en`), never inferred from
  the job. Missing → generation is blocked (`LANGUAGE_MISSING`).
- **Fixed, deterministic wording** (server-built): subject (`Bewerbung als
  <title>` / `Application for the position of <title>`), salutation (`Sehr
  geehrte Damen und Herren,` / `Dear Hiring Team,` — no invented recipient),
  closing formula, the signature (name from the resume, else the account name),
  and — when chosen — the salary sentence. If a title were unavailable, a
  generic subject is used instead of an invented one.
- **Body paragraphs** (AI): `opening` (first, exactly one), `role_fit`,
  `experience` (at least one), `skills`, `motivation`, `closing` (last, exactly
  one). `experience`, `skills` and `motivation` must cite facts;
  `experience`/`skills` must cite *resume* facts, not only application facts.
- **No filler claims.** Rejected: knowledge of or admiration for the company or
  its culture, values or products; "market leader"-style claims; relocation and
  personal circumstances; a claim to hold the target job's title; a claim of
  past employment at the company applied to (unless the resume says so);
  skills or tools the job asks for that the cited resume facts don't contain.
- **Availability and notice period** may be mentioned only from the
  application's own facts.
- **Match** is guidance only (what to emphasize, what never to claim). A match
  fact can never support a claim about the candidate — a skill in the letter
  must be in a cited *resume* fact, whatever the match says.

## 9. Salary

V0.7 stores `salary_expectation`, `salary_currency` and
`mention_salary_in_letter`.

| `mention_salary_in_letter` | Salary expectation set? | Letter |
| --- | --- | --- |
| `true` | yes | one fixed sentence, built from the application's own figure: *Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr.* / *My salary expectation is 78,000 EUR gross per year.* |
| `true` | no | **blocked**: `SALARY_EXPECTATION_REQUIRED` |
| `false` | any | no salary anywhere |
| `null` (undecided) | yes | **blocked**: `SALARY_DECISION_REQUIRED` — the UI asks for a decision |
| `null` | no | nothing to decide; no salary |

The decision is never assumed. The AI never sees the figure and **may not
mention pay at all**: any salary word, currency or amount in AI-written text is
rejected (`SALARY_MENTION_NOT_ALLOWED`), so the only salary in a letter is the
server's sentence — and a CV never has one. A CV does not depend on the
decision. A salary stated in the *posting* is never used.

## 10. Truth validation

`services/documents/validation.py` (checks in `claims.py`), reusing V0.2's
fact-checking approach — normalized matching against source text
(`SourceText`) — and the V0.6 explanation validator's taxonomy finders. No
AI, no network, deterministic. A document that fails is rejected whole; the
report lists *what and where*, never the rejected document.

**Structure and traceability**

| Code | Rejects |
| --- | --- |
| `MISSING_SOURCE`, `UNKNOWN_FACT_ID`, `FOREIGN_FACT`, `JOB_FACT_AS_SOURCE` | text without citations, citations that don't exist, a bullet citing another role's fact, a job requirement cited as proof about the candidate |
| `UNKNOWN_EXPERIENCE`, `DUPLICATE_EXPERIENCE`, `MISSING_EXPERIENCE` | a role not in the resume, listed twice, or left out |
| `UNKNOWN_SKILL`, `DUPLICATE_SKILL` | a skill that is not a skill fact |
| `INVALID_STRUCTURE`, `EMPTY_TEXT`, `WRONG_LANGUAGE` | a letter without opening / closing / experience; empty text; text not in the application language (German / English marker words, only when clearly wrong) |

**Claims** — each must be supported by the facts the text *cites* (not by the
resume as a whole):

| Code | Detects |
| --- | --- |
| `UNSUPPORTED_NUMBER` | any number, percentage, date or written-out number (*twelve*, *drei*) the cited facts don't state — invented years and results |
| `UNSUPPORTED_SKILL`, `UNSUPPORTED_LANGUAGE`, `UNSUPPORTED_DEGREE` | taxonomy terms (V0.5.1) not in the cited facts |
| `UNSUPPORTED_TECHNOLOGY` | tool-shaped tokens the taxonomy doesn't know: acronyms (*ABAP*), `C++`, `S/4HANA`, `Node.js`, `PostgreSQL` |
| `UNSUPPORTED_ORGANIZATION` | `<Name> GmbH` / `AG` / `Inc.` ... not in the cited facts; past employment at the company applied to |
| `UNSUPPORTED_NAME` | (English) capitalized names mid-sentence that the facts don't contain |
| `UNSUPPORTED_ROLE_CLAIM` | leadership / management, *senior / junior / principal*, executive titles, founding, mentoring, architect — in German and English |
| `UNSUPPORTED_CREDENTIAL` | certification, licence, degree, MBA, PhD, *Zertifizierung*, *Diplom* ... |
| `UNSUPPORTED_ACHIEVEMENT` | awards, publications, patents, "doubled", "million" ... |
| `UNSUPPORTED_EXPERIENCE_CLAIM` | "decades of", *langjährige* ... |
| `COMPANY_KNOWLEDGE_CLAIM`, `PERSONAL_CIRCUMSTANCE_CLAIM` | (letter) admiration / knowledge of the company, relocation, family ... |
| `SALARY_MENTION_NOT_ALLOWED` | any pay talk in AI text |

The job's own title (only where it *refers to the position* — "the position of
…", "… role", "Stelle als …"), company and location are blanked out of a
letter before claims are checked, so "applying for the Senior Project Manager
position at Example GmbH" is fine while "I am a Senior Project Manager" is a
claim. A CV is never masked.

## 11. Provenance

Every document records: `document_type`, `application_id`, `resume_id` (+ profile
and version number), `application_context_snapshot_id`, `input_hash`,
`prompt_version`, `ai_provider`, `ai_model`, `created_at` / `completed_at`, the
validation report and `source_facts`. The provider is whatever sits behind
`AIProvider` — nothing in V0.8 is coupled to Anthropic. The API exposes this
provenance but never prompts or credentials.

## 12. Regeneration and versions

`POST .../documents/cv` and `.../cover-letter` always create a **new attempt**.
A validated one becomes version *n + 1* and the current document; earlier
versions are untouched and stay readable; rejected and failed attempts keep no
version number (they use none up) and never become current. The history is
the list of attempts. A rejected regeneration leaves the previous version
current.

CV and letter are independent: separate endpoints, separate version counters,
and generating one never calls the AI for the other. Regeneration is never
automatic — only an explicit request calls the provider.

## 13. AI originals and user edits

V0.8 deliberately has **no editor** and no PATCH for documents. A document's
`content` is the AI's validated original and is write-once. When editing arrives
it will live in its own column/version, clearly marked *edited by you*, next to
the original — the V0.2 principle that AI output and user edits stay
distinguishable. (The facts a user wants changed belong in the resume review,
and the next generation then picks them up.)

## 14. Failure handling

Every failure is a safe application-level error; raw provider errors, prompts and
unvalidated output are never returned or logged (logs hold codes and exception
types only).

| Situation | HTTP | Code | Stored as |
| --- | --- | --- | --- |
| no resume / not confirmed / no facts / no language / unsupported language / no context | 409 | `RESUME_MISSING`, `RESUME_NOT_CONFIRMED`, `RESUME_FACTS_MISSING`, `LANGUAGE_MISSING`, `LANGUAGE_NOT_SUPPORTED`, `APPLICATION_CONTEXT_MISSING` | nothing |
| letter: salary undecided / mentioned but missing | 409 | `SALARY_DECISION_REQUIRED`, `SALARY_EXPECTATION_REQUIRED` | nothing |
| one is already running | 409 | `GENERATION_IN_PROGRESS` | nothing |
| AI not configured / unavailable / rate limited / timeout | 503 / 503 / 429 / 504 | `AI_NOT_CONFIGURED`, `AI_UNAVAILABLE`, `AI_RATE_LIMITED`, `AI_TIMEOUT` | `FAILED` |
| malformed output, schema failure, empty output | 502 | `AI_INVALID_RESPONSE`, `AI_EMPTY_RESPONSE` | `FAILED` |
| context too long for the model / provider rejected credentials / unknown AI error | 502 | `AI_CONTEXT_LIMIT`, `AI_AUTH_FAILED`, `AI_ERROR` | `FAILED` |
| truth validation failed | 502 | `DOCUMENT_REJECTED` (details: `violations`) | `REJECTED` + report |
| unexpected exception | 500 | `GENERATION_ERROR` | `FAILED` |
| request cancelled | — | `GENERATION_CANCELLED` | `FAILED` |

There is no fake "successful" fallback: with no provider, generation fails with
*"Document generation is not available because no AI provider is configured"*
(or *"Generation failed. Please try again."*) — never fabricated content.

## 15. API

All endpoints require authentication and are scoped to the current user.

```
GET    /applications/{id}/documents                 every attempt (newest first) + what's needed next
GET    /applications/{id}/documents/{document_id}   one document: content, validation, source facts
POST   /applications/{id}/documents/cv              generate a new tailored CV version (no body)
POST   /applications/{id}/documents/cover-letter    generate a new Anschreiben version (no body)
```

`GET .../documents` returns `items` (summaries: type, status, `version_number`,
`is_current`, `is_outdated`, language, resume version, snapshot id, `input_hash`,
prompt version, provider, model, `validation_status`, `violation_count`, error
code and message, timestamps) plus `tailored_cv` and `cover_letter`:
`{can_generate, blockers: [{code, message}]}`. The detail adds `content`,
`validation` and the `source_facts` the content cites. `POST` returns `201` with
the new document, or an error (§14); both generation endpoints take **no
body**, so nothing — language, a fact, a salary — can be smuggled into a
request. There is no `DELETE`.

## 16. Authorization

`current_user → owns Application → owns UserJob`, enforced on every operation:
another user's application, a document of another application, and an unknown
id are all the same `404`; generation for another user's application never
reaches the AI. The database adds the composite ownership key (§2). A resume
reference that is not the user's own is never followed (generation reports
`RESUME_MISSING`).

**Privacy.** A document is the user's own data: its `content` and `source_facts`
hold the resume facts that were used, and they are removed with the application
or the user (cascade). Deleting a resume keeps its documents (users own what
was generated) but clears the reference, leaving the resume version number.
Resume facts go to the configured AI provider only when the user explicitly
generates a document; contact details, notes and the salary figure never do.
Logs contain codes and exception types, never resume or document content.

## 17. Frontend

The **Documents** section of `/applications/[id]` (`ApplicationDocuments`,
`DocumentViewer`; `lib/api/documents.ts`, `lib/documents.ts`,
`types/documents.ts`) shows, per document type: its status (*Not generated* /
*✓ Generated · Version n · Generated today · ✓ Checked against your resume*),
**Generate** / **View** / **Regenerate**, the version history, the blockers the
server reports (with a link to Application preparation), a rejected attempt with
plain-language reasons ("A tool or technology your resume doesn't state:
“Kubernetes”") and that nothing was saved, an *Outdated* notice, and a loading
state while generating. Generating is disabled while the form has unsaved
changes (documents use saved data). The viewer shows the document with a
*Sources* disclosure under each AI-written text, the validation result and the
provenance (version, language, resume version, provider, model, prompt version).

## 18. Database and migration

Migration `f1a6d3c8b2e4` (`v0_8_tailored_documents`), purely additive: the two
enum types, the `generated_documents` table with its constraints and indexes, and
`uq_applications_id_user_id` (trivially satisfied: `id` is the primary key; it
is the target of the composite ownership key). No existing table, column, row or
index changes, so V0–V0.7 data survives untouched and nothing needs a backfill.
`downgrade()` drops exactly what was added — generated documents are lost; the
V0.7 schema and every V0.7 row come back exactly. Verified: upgrade →
downgrade → upgrade → `alembic check`, with realistic V0.7 application data
(`tests/test_migrations.py`).

## 19. Testing

No test calls a real AI. `FakeDocumentAI` (`tests/document_helpers.py`) answers
the resume parser with the canned fixture and a document request by restating
the exact facts it is shown (faithful), and tests make it unfaithful on purpose
to prove that invented claims are rejected. The browser E2E uses the same fake
as a stub provider (`backend/e2e/serve.py`; visible as provider `e2e-stub`).

| Area | Where |
| --- | --- |
| fact catalogue, determinism, what the AI is shown, salary sentence, prompt fingerprints | `tests/test_document_context.py` |
| truth validation (each invented-claim category, paraphrase and supported claims, structure, letter rules) | `tests/test_document_validation.py` |
| generation, versions, regeneration, provenance, snapshot / resume version, salary, language, failures, in-flight, security, database rules | `tests/test_documents_api.py` |
| migration roundtrip with V0.7 data | `tests/test_migrations.py` |
| Documents section, generate / regenerate, loading, failure, rejection, versions, viewing, salary blockers, language, polling | `frontend/__tests__/ApplicationDocuments.test.tsx`, `documentsLib.test.ts`, `ApplicationWorkspace.test.tsx` |
| real Chromium flow | `frontend/e2e/v08-tailored-documents.e2e.ts` |

## 20. Limitations

- **Validation is lexical.** It catches invented employers, tools, numbers,
  credentials, titles and pay, and the structure (mandatory citations,
  per-role facts) constrains the rest. It cannot prove that a sentence is
  *semantically* supported: a bullet that cites a real fact but subtly
  overstates it without using a flagged word can pass. The taxonomy knows 84
  skills; other tools are caught by their shape (acronyms, `C++`, camelCase), not
  by name, so an unusual lower-case tool name can slip through.
- Some legitimate text is rejected ("the German market" counts as a claim to
  speak German; "managed" when the cited fact says "led"): a rejection
  costs a retry, a missed invention costs the user's trust, so the checks lean
  strict. There is no "drop the offending bullet" repair.
- The name check applies to English text only (German capitalizes every noun).
- The language check flags only clearly wrong German / English text.
- Only German and English; letters are addressed generically (*Sehr geehrte
  Damen und Herren*) — no recipient is invented.
- Documents are on-screen only: no PDF / DOCX export, no editor, no delete.
- Facts come from the confirmed resume only; skills, languages and experience
  the user declared in their V0.1 profile but not in the resume are not used.
- Generation is a single synchronous request (bounded by the AI timeout), not a
  background job.
- Match guidance is optional and best-effort (used only when current).

## 21. Scope and what's deferred

V0.8 does **not** include, and nothing in it accidentally implements:

| Deferred to | Feature |
| --- | --- |
| later | editing generated documents (kept apart from the AI original), PDF / DOCX export, deleting versions |
| V0.9 | AI answers to application questions |
| V0.10 | browser automation, auto-fill |
| V0.11 | review and submission |
| V0.12 | the full application lifecycle and tracking |

It also adds no new job source, no new matching algorithm, no semantic
matching or embeddings, no automatic salary recommendation and no automatic
language inference.
