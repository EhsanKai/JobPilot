# Resume Intelligence (V0.2)

Resume Intelligence turns an uploaded resume into a structured, evidence-backed, **user-confirmed**
Resume Profile that later JobPilot releases (matching, gap analysis, tailored CVs, application
generation) can rely on.

V0.2 does **not** match jobs, compute match percentages, search for jobs, or generate documents.

---

## 1. Principles

1. **Never invent facts.** The parser only extracts. Anything it cannot support is `null`, an
   empty list, or `"unknown"`, never a guess.
2. **Keep evidence.** Every extracted entity carries verbatim quotes from the resume. The
   server, not the AI, checks each quote against the extracted text.
3. **Keep confidence.** `high` / `medium` / `low` describes how certain the *extraction* is. It
   says nothing about how good the candidate is at something.
4. **Separate resume facts from user preferences.** The Resume Profile never overwrites the V0.1
   user profile. Disagreements are reported as conflicts.
5. **Require user confirmation.** AI output starts as `pending`. It becomes trusted only after the
   user reviews every item and confirms.
6. **Keep the original untouched.** Uploaded files are never modified or replaced. Results live in
   separate rows.
7. **Make the AI provider replaceable.** Business logic depends on an interface, not a vendor SDK.
8. **Make the parser versioned and re-runnable.** Every result records its parser version and
   source file hash.

---

## 2. Processing architecture

```
POST /resumes/{id}/process                      (HTTP request, returns quickly)
  │  ownership check (404 for other users' resumes)
  │  concurrency guard (409 while a run is in progress; stale runs > 15 min may restart)
  │  file hash (SHA-256, computed at upload; lazily for pre-V0.2 files)
  │  reuse check: same file + same parser version already analyzed? → 200, no AI call
  │  create ResumeProfile(status=processing); resume.status = extracting
  ▼
FastAPI BackgroundTask: run_processing()        (own DB session)
  1. Extraction  read original via FileStorage → PdfTextExtractor / DocxTextExtractor
                 → normalized text (or OCR_REQUIRED / EXTRACTION_FAILED / EMPTY_DOCUMENT)
                 → length guard (RESUME_TOO_LONG; no silent truncation)
                 resume.status = extracted
  2. Parsing     resume.status = parsing
                 AIResumeParser → AIProvider.generate_structured(schema=ParsedResume)
                 resume.status = parsed
  3. Validation  build_profile_data(): truth checks, evidence verification, skill merging
  4. Persistence raw_data + reviewed_data stored; older results superseded
                 resume.status = needs_review
  ▼
User review (accept / edit / reject / reset per item, accept-all) → confirm → resume.status = confirmed
```

Each stage has its own module under `backend/app/services/resume_intelligence/`:

| Module                  | Responsibility                                                  |
| ----------------------- | ---------------------------------------------------------------- |
| `extraction.py`         | `ResumeTextExtractor` interface, PDF/DOCX implementations, text normalization, OCR detection |
| `parser.py`             | `ResumeParser` interface, `AIResumeParser`, `AI_RESUME_PARSER_VERSION` |
| `validation.py`         | Deterministic truth guard, `SourceText` matching, skill normalization |
| `processing_service.py` | Orchestration, status transitions, reuse, failure handling      |
| `review_service.py`     | Accept / edit / reject / reset / confirm / reopen, summaries     |
| `conflicts.py`          | Derived metrics and resume-vs-profile conflict detection         |

The API router (`app/api/v1/resumes.py`) only parses requests and calls these services.

The frontend polls `GET /resumes/{id}/profile` every 2 seconds while `is_processing` is true. It
stops after about 3 minutes and offers a manual refresh, so it never waits forever on a slow AI
request. No Redis or Celery queue is needed for V0.2. Moving `run_processing` to a worker queue
later only changes how it is scheduled.

### Supported formats

| Format | Library       | Notes                                                                   |
| ------ | ------------- | ----------------------------------------------------------------------- |
| PDF    | `pypdf`       | Pages with no text layer are treated as scanned. Fewer than 40 alphanumeric characters in total (or under 20 per page) yields `OCR_REQUIRED`. Encrypted PDFs are tried with an empty password. |
| DOCX   | `python-docx` | Paragraphs, tables (cells joined with `|`), and headers, in document order |

OCR is **not** implemented. A scanned PDF gets `ocr_required` and the message *"This PDF does not
contain extractable text. OCR support will be added in a future version."* An empty profile is
never produced silently.

---

## 3. AI provider abstraction

```
app/ai/
├── provider.py           AIProvider (abstract) + AIError hierarchy
├── factory.py            get_ai_provider(): reads settings, never raises
├── providers/
│   ├── anthropic.py      AnthropicProvider: the only module importing a vendor SDK
│   └── disabled.py       DisabledAIProvider: fails fast with AI_NOT_CONFIGURED
└── prompts/
    ├── __init__.py       load_prompt(name)
    └── resume_parser_v1.txt
```

```python
class AIProvider(ABC):
    name: str            # provenance, e.g. "anthropic"
    model: str | None    # provenance, e.g. "claude-sonnet-5-5"

    async def generate_structured(self, *, system: str, prompt: str, schema: type[T]) -> T: ...
```

A provider must return an instance of `schema` that Pydantic has validated, or raise an `AIError`
subclass. It never returns free-form prose or an unvalidated dict.

**Anthropic implementation.** It uses the Messages API's native structured outputs
(`output_config.format = {"type": "json_schema", "schema": transform_schema(ParsedResume)}`),
then re-validates the returned JSON with `ParsedResume.model_validate_json`. The SDK retries
connection errors, 429 and 5xx responses (`AI_MAX_RETRIES`). Timeouts come from
`AI_TIMEOUT_SECONDS`.

**Adding a provider** (OpenAI, Google, a local LLM): implement `AIProvider` in
`app/ai/providers/<name>.py`, map that SDK's exceptions to the `AIError` hierarchy, and add a
branch in `factory.py`. The parser, services, API and frontend stay unchanged.

### Configuration (environment only)

| Variable                  | Default             | Meaning                                       |
| ------------------------- | ------------------- | --------------------------------------------- |
| `AI_PROVIDER`             | `anthropic`         | `anthropic` or `disabled`                     |
| `ANTHROPIC_API_KEY`       | unset               | Without it, analysis fails gracefully with `AI_NOT_CONFIGURED` |
| `AI_MODEL`                | `claude-sonnet-5-5` | Model id, stored with every result            |
| `AI_TIMEOUT_SECONDS`      | `120`               | Per-request timeout                           |
| `AI_MAX_RETRIES`          | `2`                 | SDK retries for transient errors              |
| `AI_MAX_OUTPUT_TOKENS`    | `16000`             | Output budget; truncation is treated as invalid output |
| `RESUME_MAX_TEXT_CHARS`   | `60000`             | Longer resumes are rejected, never truncated  |
| `RESUME_PROCESSING_STALE_MINUTES` | `15`        | When a stuck "in progress" run may be restarted |

### Failure handling

Every failure leaves the run in a terminal state with a user-safe message. Nothing is left stuck
in progress, and no provider internals are exposed.

| Code                  | Cause                                                       |
| --------------------- | ----------------------------------------------------------- |
| `OCR_REQUIRED`        | PDF without a text layer                                    |
| `EXTRACTION_FAILED`   | Corrupted / unreadable / password-protected file            |
| `EMPTY_DOCUMENT`      | DOCX without text                                           |
| `FILE_MISSING`        | Original file no longer in storage                          |
| `RESUME_TOO_LONG`     | Extracted text exceeds `RESUME_MAX_TEXT_CHARS`              |
| `AI_NOT_CONFIGURED`   | No provider / API key configured                            |
| `AI_AUTH_FAILED`      | 401 / 403 from the provider                                 |
| `AI_RATE_LIMITED`     | 429 after retries                                           |
| `AI_TIMEOUT`          | Request timed out                                           |
| `AI_UNAVAILABLE`      | Connection error or 5xx                                     |
| `AI_CONTEXT_LIMIT`    | Prompt too long for the model                               |
| `AI_EMPTY_RESPONSE`   | No content returned                                         |
| `AI_INVALID_RESPONSE` | Output failed schema validation, was truncated, or was refused |
| `NO_RESUME_CONTENT`   | Valid output but nothing was extracted                      |
| `PROCESSING_ERROR`    | Unexpected exception (logged by type and location only)      |

If a **re**-processing run fails, the previous good result stays current. The resume keeps that
result's status, and the failure is reported through `latest_attempt`.

---

## 4. Parser and prompt

`AIResumeParser` depends only on `AIProvider`. It sends:

- **system**: the versioned prompt `app/ai/prompts/resume_parser_v1.txt`
- **user**: the resume text wrapped in `<resume_text>…</resume_text>`
- **schema**: `ParsedResume`

The prompt tells the model to extract only supported facts and never invent anything. Missing
information must be null or `"unknown"`. Evidence must be quoted verbatim. Explicit facts are
marked `"explicit"` and inferences `"inferred"`. The model must not write a summary, must not
derive skill levels from years of experience or CEFR levels from "fluent", and must not
translate degrees. The resume text is treated as data, so instructions embedded in a resume are
ignored (a prompt-injection defense).

**Versioning.** `AI_RESUME_PARSER_VERSION = "resume_parser_v1"`. Released prompt files are never
edited in place. A behavior change gets a new `resume_parser_v2.txt` and a version bump. Results
from older versions stay valid, and identical files are re-analyzed automatically when the
parser version differs.

---

## 5. Extracted data model

### 5.1 Parser output contract: `ParsedResume`

Defined in `app/schemas/resume_profile.py`. Field descriptions double as extraction
instructions, since they are part of the JSON schema sent to the model.

```
ParsedResume
├── document_language
├── personal_information   full_name, email, phone, location, linkedin_url, github_url, portfolio_url
│                          (each {value, confidence, basis, evidence} or null)
├── summary                the resume's own summary, verbatim, or null
├── work_experience[]      company, job_title, location, start_date, end_date, date_text,
│                          is_current, description, responsibilities[], technologies[],
│                          achievements[], confidence, basis, evidence[]
├── education[]            institution, degree, field_of_study, start_date, end_date,
│                          date_text, location, description, confidence, basis, evidence[]
├── skills[]               name (canonical), aliases[] (as written), category,
│                          proficiency (explicit level or "unknown"), proficiency_evidence,
│                          confidence, basis, evidence[]
├── languages[]            language, as_written, proficiency (as stated), cefr_level
│                          (only if literally stated), confidence, basis, evidence[]
├── certifications[]       name, issuer, date, credential_id, url, ...
├── projects[]             name, description, role, technologies[], dates, url, ...
└── additional_information[]  category (award | publication | volunteering | interest |
                              driving_license | work_authorization | availability | other), value, ...
```

Dates are `"YYYY"` or `"YYYY-MM"`, as precise as the resume states them. Values like "03/2021"
are re-formatted to "2021-03". Missing months are never filled in. The original wording is kept
in `date_text`.

### 5.2 Persisted document: `ResumeProfileData`

This has the same entities, plus per item:

- `id`: stable item id (used by the review API)
- `review_status`: `pending | accepted | edited | rejected`
- `user_edited_fields`: which fields the user changed
- `evidence[].verified`: set by the server, never by the AI

Personal information is stored as a list of `{field, value, ...}` items so each field can be
reviewed on its own.

### 5.3 Database

```
resumes                      (extended)
  version_number             1, 2, 3 … per user (unique with user_id)
  file_hash                  SHA-256 of the original file
  status                     resume_processing_status enum (see §8)
  processing_started_at

resume_profiles              (new; one row per processing attempt)
  resume_id, user_id
  status                     processing | needs_review | confirmed | superseded | failed | ocr_required
  parser_version, schema_version, source_file_hash, ai_provider, ai_model
  reused_from_profile_id
  extraction_method, page_count, extracted_text
  raw_data      JSONB        validated parser output after truth checks, never edited
  reviewed_data JSONB        same document with the user's review decisions
  warnings      JSONB        what the truth guard changed, and why
  error_code, error_message  user-safe failure info
  started_at, processed_at, confirmed_at
```

**Why JSONB rather than one table per entity.** A Resume Profile is a document-shaped, versioned
snapshot of one parser run, with nested evidence. Its shape will change with parser versions
(tracked by `schema_version`) without needing a migration each time. Both documents are
validated by Pydantic on every read and write, so they can never hold malformed data. If V0.5
matching needs relational queries over confirmed skills, the confirmed profile can be projected
into normalized tables at that point. V0.1's catalog tables (`skills`, `languages`) are
deliberately left untouched, because they hold what the *user* says.

**Resume versioning.** Since V0.2 an upload creates a new version instead of deleting the
previous one. `GET /resumes/current` returns the newest version, and `GET /resumes` lists all of
them. Deleting a resume removes its file and, by cascade, its profiles.

**Original vs. processed data.** The original file is only ever read
(`FileStorage.read`). All derived data lives in `resume_profiles`, so any resume can be
re-processed later.

---

## 6. The truth guard (validation stage)

`build_profile_data()` checks the parser output against the extracted text deterministically,
without AI, before anything is stored. Every adjustment is recorded as a `ProfileWarning` and
shown to the user in the review UI.

| Check                                   | Action                                              |
| --------------------------------------- | --------------------------------------------------- |
| Evidence quote not found in text        | `verified=false`; entity confidence capped at `medium` |
| Entity name (company, title, skill or alias, institution, …) not found and no verified evidence | Kept for review, confidence set to `low`, `not_found_in_resume` warning |
| Name not found but evidence verified    | `basis` set to `inferred` (e.g. a canonical skill name) |
| Personal detail (email, phone, URL, name, location) not in text | **Dropped**                       |
| Summary not in the resume (i.e. written by the model) | **Dropped**: V0.2 never generates summaries |
| Technology on a role or project not in text | Removed from that entity                          |
| Date whose year doesn't appear          | Cleared                                             |
| Skill proficiency without a verified quote | Reset to `unknown`                               |
| CEFR level or language proficiency not stated | Removed                                       |
| `is_current` together with an end date  | `is_current` cleared, warning                       |
| Nothing extracted at all                | Run fails with `NO_RESUME_CONTENT`                  |

Matching ignores case, whitespace, quote and dash styles, and (for longer terms) punctuation, so
"S/4HANA" matches "S4 HANA". Short terms like "C", "R" or "Go" must match as whole words.

**Skill normalization.** Obvious spelling variants of the same skill are merged through a small,
conservative alias map ("ABAP", "SAP-ABAP" → "SAP ABAP"; "JS" → "JavaScript";
"Postgres" → "PostgreSQL"). Their written forms are kept in `aliases` and their evidence is
combined. The map never broadens meaning: "SAP" stays "SAP". "C", "C++" and "C#" remain
distinct.

---

## 7. Confidence and evidence in the UI

- Confidence is a small colored dot with "High / Medium / Low confidence". It is deliberately
  understated.
- An **Inferred** tag marks derived facts.
- **Why?** expands the evidence quotes, each marked "✓ Found in your resume" or "⚠ Not found
  word-for-word".
- **View source** loads the extracted text (`GET /resumes/{id}/profile/source`) on demand and
  shows the quote highlighted in its surrounding context.

---

## 8. Processing statuses

`resumes.status` (Postgres enum `resume_processing_status`):

```
uploaded → extracting → extracted → parsing → parsed → needs_review → confirmed
                ↘ ocr_required          ↘ failed
```

| Status         | Meaning                                                          |
| -------------- | ---------------------------------------------------------------- |
| `uploaded`     | Stored, never analyzed                                           |
| `extracting`   | Reading text from the file                                       |
| `extracted`    | Text extracted                                                   |
| `parsing`      | Waiting for the AI parser                                        |
| `parsed`       | Parser returned; validating                                      |
| `needs_review` | A result exists and awaits review                                |
| `confirmed`    | The user confirmed the result: trusted Resume Profile            |
| `failed`       | The latest attempt failed and no earlier result exists           |
| `ocr_required` | The PDF has no extractable text                                  |

The migration converts the V0 `resume_status` type (UPLOADED / PROCESSING / PROCESSED / FAILED)
into this new, distinctly named type with an explicit `USING` mapping, and drops the old name.
The downgrade recreates it. Every new enum type is created and dropped explicitly, which
addresses the ENUM reuse and naming problems seen in V0 and V0.1.

---

## 9. Review and confirmation

| Endpoint                                          | Purpose                                    |
| ------------------------------------------------- | ------------------------------------------ |
| `GET  /resumes/{id}/profile`                      | Resume, `latest_attempt`, `is_processing`, current profile (data, raw_data, warnings, summary counts, conflicts, derived metrics) |
| `GET  /resumes/{id}/profile/source`               | Extracted text, for "View source"           |
| `PATCH /resumes/{id}/profile/items/{item_id}`     | `{"action": "accept" \| "reject" \| "reset"}` or `{"action": "edit", "changes": {...}}` |
| `POST /resumes/{id}/profile/accept-pending`       | Explicit bulk accept of the remaining items |
| `POST /resumes/{id}/profile/confirm`              | Requires 0 pending items (else 409 with `pending_items`) |
| `POST /resumes/{id}/profile/reopen`               | Confirmed → needs_review, for corrections   |

Edits are validated per section (`EDIT_MODELS`). Only fact fields can be edited, never
confidence, evidence or ids. Dates must be `YYYY` or `YYYY-MM`. `reset` restores the item from
`raw_data`. A confirmed profile is read-only until reopened. Review actions return 409 while the
resume is being re-processed.

**Raw vs. confirmed.** `raw_data` always holds what the parser produced. `reviewed_data` with
status `confirmed` is the trusted Resume Profile. Within it, the accepted and edited items are
trusted, and rejected items are excluded from derived metrics and conflict checks.

---

## 10. Resume profile vs. user profile, and conflicts

The V0.1 profile is *what the user tells JobPilot*. The Resume Profile is *what the resume
says*. They are never merged. `conflicts.py` compares them on read, so the result always
reflects current data:

| Field                | Conflict when                                                        |
| -------------------- | -------------------------------------------------------------------- |
| Name                 | Account first and last name aren't both contained in the resume name |
| Email                | Resume email differs from the account email                          |
| Phone                | Trailing 9 digits differ                                             |
| Location             | Neither contains the other                                           |
| LinkedIn             | URLs differ (ignoring scheme, `www.`, trailing slash)                |
| Current job title    | The resume's current role ≠ profile `current_job_title`              |
| Years of experience  | Resume-derived years (merged, non-overlapping date ranges) differ by ≥ 1 from the profile |

Each conflict exposes `profile_value`, `resume_value` and `potential_conflict: true`. Neither side
is changed, and the review UI points the user to the Profile page to resolve it.

Derived experience uses explicit dates only. Year-only dates count from mid-year and mark the
total as approximate. Roles without a start date, or without an end date that aren't marked
current, make the total `experience_is_partial`.

---

## 11. Reprocessing and cost control

- **Automatic re-runs never happen.** Analysis only starts when the user clicks **Analyze
  Resume**.
- `POST /process` with the same file hash and parser version as an existing successful result
  returns **200 `reused: true`** without calling the AI.
- If the identical file was uploaded again as a new version, the earlier parser output is copied
  into a fresh, *unreviewed* profile (`reused_from_profile_id`). Review decisions are not copied,
  because each version is confirmed on its own.
- **Reprocess Resume** (`{"force": true}`, behind an inline confirmation in the UI) runs the full
  pipeline again on the unchanged original file. On success the previous result becomes
  `superseded`. On failure it stays current.
- A future parser version re-processes old resumes simply because its version differs.

---

## 12. Privacy

Resume files and parsed data are sensitive personal information.

- **Never logged:** resume text, prompts, parser output, evidence, API keys, tokens. Processing
  logs contain only ids, status and error codes, counts, durations and exception *types*.
- `ValidationError` messages embed input values, so they are summarized as locations and error
  types only (`summarize_validation_error`). Unexpected exceptions are logged by type and
  code location, never with `logger.exception()`, because messages such as SQL errors can embed
  data.
- Provider errors are mapped to generic codes. SDK error bodies are discarded.
- Every endpoint is owner-scoped. Other users' resumes return **404** (not 403), so ids can't be
  probed. No route or static mount serves stored files. `LocalFileStorage` refuses references
  that escape its root.
- Resume text goes to the configured AI provider only when the user explicitly starts an
  analysis. The UI says so next to the button.
- Deleting a resume deletes its file and cascades to all its profiles, including the extracted
  text.

---

## 13. Testing strategy

No test calls a real AI API. The conftest forces `AI_PROVIDER=disabled` and injects a
`FakeAIProvider` that returns canned responses and validates them exactly like a real provider.

| Area                         | Where                                                      |
| ---------------------------- | ---------------------------------------------------------- |
| PDF / DOCX extraction, OCR detection, normalization | `tests/test_resume_extraction.py` (PDFs and DOCXs generated in-test) |
| Prompt, parser, schema validation, date normalization | `tests/test_resume_parser.py`                |
| Truth guard (hallucinated email, generated summary, unverified skills, proficiency, CEFR, technologies, dates, skill merging) | `tests/test_resume_parser.py` |
| Derived metrics and conflicts | `tests/test_resume_parser.py`                             |
| Anthropic provider: request shape, bad responses, exception mapping (mocked client) | `tests/test_resume_parser.py` |
| Processing API, status transitions, every failure code, reuse, reprocessing, versioning, review, confirm/reopen, authorization, no public file access, no sensitive logging | `tests/test_resume_intelligence_api.py` |
| Frontend analysis, review, dashboard, upload | `frontend/__tests__/ResumeAnalysis.test.tsx`, `ResumeReview.test.tsx`, `DashboardContent.test.tsx`, `ResumeUpload.test.tsx` |

Deterministic fixtures: `backend/tests/fixtures/resume/sample_resume.txt` (a fictional resume)
and `expected_parsed_resume.json` (the mocked parser output for it).
