# V0.9 — Application Question AI + Human Control at Every Stage

V0.9 answers *"The employer asks questions — help me answer them, without making
anything up, and without taking the application out of my hands."* An
Application (V0.7) can hold the questions an employer asks; JobPilot suggests
answers from structured application data or from the confirmed resume, says
**"needs your input"** when it cannot, and the user stays in control of every
word that ends up in the application. V0.9 also adds **✨ Humanize** for the
Anschreiben and the editing, copying and photo tools that make "I can finish
this without the AI" true for the documents too.

```
Application
 ├── ApplicationQuestion (many)             what the employer asked        (V0.9; manual now, discovered in V0.10)
 │     ├── ApplicationQuestionAnswer        the current state, one per question
 │     │     ├── suggestion  (ai_*)         deterministic or AI, validated        — never written by the user
 │     │     └── answer      (final_*)      accepted / written by the user        — never written by the AI
 │     └── ApplicationQuestionGeneration    every attempt, write-once (audit trail)
 └── GeneratedDocument versions             CV / Anschreiben                (V0.8)
       origin: ai · humanized · user_edited                                  (V0.9)
```

## Contents

1. [Human Control at Every Stage](#1-human-control-at-every-stage)
2. [Scope](#2-scope)
3. [The data model](#3-the-data-model)
4. [Answer states](#4-answer-states)
5. [Deterministic answers (no AI)](#5-deterministic-answers-no-ai)
6. [AI suggestions](#6-ai-suggestions)
7. [Truth validation of an answer](#7-truth-validation-of-an-answer)
8. [Provenance](#8-provenance)
9. [Human Control in the question workflow](#9-human-control-in-the-question-workflow)
10. [✨ Humanize](#10-humanize)
11. [Editing, copying and the CV photo](#11-editing-copying-and-the-cv-photo)
12. [API](#12-api)
13. [Authorization and security](#13-authorization-and-security)
14. [Frontend](#14-frontend)
15. [Database and migration](#15-database-and-migration)
16. [Testing](#16-testing)
17. [Limitations](#17-limitations)
18. [Scope and what's deferred](#18-scope-and-whats-deferred)

## 1. Human Control at Every Stage

> **The user always owns the application and must be able to take over manually at
> any stage.** AI never creates a dependency the user cannot continue without.

At every stage the user can inspect AI output, edit it, reject it, replace it with
their own content, copy it, use it outside JobPilot, continue manually and return
to AI assistance later. Concretely:

| Artifact | The AI's side | The user's side | The rule |
| --- | --- | --- | --- |
| Application question | suggestion (`ai_*`) | answer (`final_*`) | a new suggestion never changes the answer |
| Tailored CV / Anschreiben | `ai` / `humanized` versions | `user_edited` versions | a new AI version never *silently* supersedes the user's edit, and never deletes it |
| CV photo | — | the user's own file | no AI operation can read, write or replace it |
| Browser-filled field (V0.10) | filled value | what the user sees and may edit before submission | same pattern; only approved answers are filled, a field the user edited is never overwritten |

This is the **Human Control Boundary**: every AI-generated artifact has a
user-owned final representation, kept apart from the AI's, and the only way
content crosses the boundary is an explicit user action (accept, save, confirm).
It is a *pattern*, deliberately not a framework: each artifact implements it in
the shape that fits (columns on the answer, `origin` on a document version).

Consequences that are enforced in code, not just in the UI:

- A client cannot send a suggestion, a validation result, provenance or a source
  fact; `accept` copies the **server-held, validated** suggestion.
- `generate` / `regenerate` only ever replace the *suggestion*. If a user answer
  exists they require an explicit acknowledgement (`409
  EXISTING_ANSWER_CONFIRMATION_REQUIRED`), and even then the answer is untouched.
- Taking a new suggestion over a different answer needs `replace_existing`
  (`409 ANSWER_WOULD_BE_REPLACED`).
- A user answer is stored **exactly as written**: trimmed of surrounding
  whitespace, never rewritten, and never truncated — over the character limit it
  is refused (`422 ANSWER_TOO_LONG`).
- Editing the *question* never silently changes or cuts an answer: a lower limit or
  a removed option the answer would no longer satisfy is refused (`409
  ANSWER_CONFLICT`).
- Regenerating a CV or letter whose **current version is the user's own edit**
  needs `acknowledge_edited_current` (`409 EDITED_DOCUMENT_CONFIRMATION_REQUIRED`).
  The edited version is never deleted or changed; it stays in the history.
- Every generation is optional. Without any AI provider, questions can still be
  answered by hand, structured ones are still suggested from the application's
  data, and documents can still be edited.

## 2. Scope

V0.9 contains: application questions (entered by the user), deterministic and AI
suggestions, their validation and provenance, the user's accept / edit / reject /
manual / clear controls, copy, **Humanize**, user edits of CV and Anschreiben as
new versions, the CV photo, and the UI for all of it.

V0.9 does **not** contain browser automation, website navigation, form filling or
submission, CAPTCHA handling, LinkedIn / Indeed / ATS integration, a full document
editor, an image editor, or a workflow engine. V0.9 questions are **entered by
the user**; V0.10 will populate the same model from browser-discovered form
fields (§3, *Source-neutral metadata*).

## 3. The data model

### 3.1 `application_questions` — `ApplicationQuestion`

| Column | Notes |
| --- | --- |
| `application_id`, `user_id` | composite FK `(application_id, user_id) → applications (id, user_id)` |
| `question_text` | as the employer wrote it; never rewritten |
| `normalized_question_text`, `question_hash` | server-computed (case, whitespace, trailing `?`/`*`); the hash is an identity for "the same question asked again" |
| `question_type` | `FREE_TEXT` `YES_NO` `SINGLE_CHOICE` `MULTI_CHOICE` `NUMERIC` `DATE` `SALARY` `UNKNOWN` |
| `required`, `max_length` | `max_length` is a hard limit: never exceeded, never truncated to |
| `options` | the choices as shown (choice types require them; a yes/no may label its two) |
| `external_key`, `field_name`, `source`, `source_metadata` | **source-neutral metadata** for V0.10 (below) |
| `sort_order` | order on the page |

Database: unique `(id, user_id)` (target of the answer's and attempts' ownership
keys), partial unique `(application_id, external_key) WHERE external_key IS NOT
NULL`, checks (`max_length ≥ 1`, `options` is a JSON array, choice types have
options), index `(application_id, sort_order, created_at)`.

**Source-neutral metadata.** `source` is a short label of whoever created the
question (`manual` now), `external_key` a stable key of the field in that source
(unique per application, so an import is idempotent), `field_name` the input's
name, and `source_metadata` a small opaque JSON object for whatever else a
discoverer knows (input type, option identifiers). Nothing is named after a
website or an ATS, so V0.10 populates these without a schema change:

```
Manual creation (V0.9) ─┐
                        ├─► ApplicationQuestion ─► Question AI ─► suggestion ─► human review ─► browser fill ─► user takeover
Browser-discovered (V0.10) ┘
```

### 3.2 `application_question_answers` — `ApplicationQuestionAnswer`

Exactly one per question (unique `question_id`), created with the question.

| Column | Notes |
| --- | --- |
| `status` | the state (§4) |
| `ai_suggestion` / `ai_selected_options` | the current suggestion; options for a choice question |
| `ai_source_fact_ids`, `ai_provenance`, `ai_generation_id` | the facts it cites, how it was produced, the attempt it came from |
| `suggestion_dismissed` | the user turned it down |
| `needs_input_reason` | set while the latest attempt found no supported answer |
| `final_answer` / `final_selected_options` | **the user's answer** |
| `answer_source` | where the final answer came from: `DETERMINISTIC` `AI` `USER` `HYBRID` |
| `basis_generation_id` | the attempt whose suggestion the final answer was accepted or edited from; NULL for an answer written from scratch |

Check constraints keep it coherent: a final answer exists exactly when the status
is `ACCEPTED` / `USER_EDITED`; a source exists exactly when a final answer does;
`ACCEPTED` implies source `DETERMINISTIC` / `AI`, `USER_EDITED` implies `USER` /
`HYBRID`; `SUGGESTED` / `REJECTED` need a suggestion; `NEEDS_INPUT` needs both
empty. Nullable JSON columns store SQL `NULL` (`none_as_null`), not JSON `null`.

**One active answer, with history.** The answer row is the single *current* state
(no two competing answers). History lives in the attempts (§3.3) and in the
`basis_generation_id` / `ai_generation_id` pointers: *what the AI suggested*, *what
the user changed* and *when regeneration happened* are all recoverable, and the
original AI suggestion is never replaced by the user's edit — they are different
columns.

### 3.3 `application_question_generations` — `ApplicationQuestionGeneration`

One **write-once** row per attempt (`ImmutableMixin`): `method` (`DETERMINISTIC` /
`AI`), `outcome` (`SUGGESTED` / `NEEDS_INPUT` / `REJECTED` / `FAILED`), the
suggestion, `source_fact_ids` and `source_facts` (id → text, so each citation stays
resolvable after the resume changes), the validation report, a user-safe error,
and the provenance of §8. A rejected or failed attempt keeps **no** suggestion — only
the reason — and never changes the answer. Check constraints: only `SUGGESTED` has
a value; an `AI` attempt records prompt version, prompt hash and provider.

## 4. Answer states

```
NOT_STARTED ─generate─► SUGGESTED ──accept──────────► ACCEPTED      (final == suggestion)
     │                      │  └─────edit / manual───► USER_EDITED   (USER or HYBRID)
     │                      └─reject─► REJECTED ─accept/edit─► …
     ├─generate─► NEEDS_INPUT          (the trusted facts do not support an answer)
     └─manual──────────────────────────────────────► USER_EDITED   (source USER)
```

The status is *derived* by one pure function (`services/questions/state.py`) after
every change and checked by the database. When the user already has an answer,
`status` stays `ACCEPTED` / `USER_EDITED` while a *new* suggestion may exist next
to it (shown as "AI suggestion" beside "Your answer"); `reject` then only marks
the suggestion dismissed. `clear` removes the user's answer; the suggestion and
the history stay.

`answer_source` tells how the user's answer came to be: `DETERMINISTIC` / `AI` —
accepted unchanged (typing out the suggestion verbatim counts as accepting it);
`HYBRID` — edited from a suggestion (and an edit of an accepted answer stays
`HYBRID`, traceable to the attempt it started from); `USER` — written from scratch.

## 5. Deterministic answers (no AI)

The AI is not called when structured data already states the answer
(`services/questions/classify.py`, `deterministic.py`): it could only invent or
alter it. Recognition is deliberately conservative (short field-label-like questions
that clearly name the topic, English and German; "why" / open questions never
match) — a miss only costs the AI path, a false match could answer the wrong
question. The result is a *suggestion* (status `SUGGESTED`, method
`DETERMINISTIC`, no prompt, no AI call), so even these are accepted by the user,
not applied.

| Topic | Source | Notes |
| --- | --- | --- |
| Salary expectation (and any `SALARY` question) | `Application.salary_expectation` | exact figure; never rounded or banded; a choice question is answered only by an option equal to the value |
| Start date | `available_from`, else the notice period | a `DATE` question is never answered from a notice period (no dates are calculated) |
| Notice period | `notice_period` | a number is given only in the unit the question asks for and the application stores (weeks → days is exact; months are never converted) |
| Work authorization / sponsorship | `work_authorization` | missing or `unknown` → needs input; "requires sponsorship" does **not** answer "are you authorized" |
| Application language | `application_language` | the application's own field — never inferred from the question's language |
| Contact details, links | the confirmed resume (accepted / edited items only) | missing → needs input |
| Total years of experience | the resume's `derived.years` | only when every role has usable dates (V0.8's rule) |

Rules common to all: **missing is never a value** (no salary → `NEEDS_INPUT`
`SALARY_MISSING`, never `0`, never "No"); the answer's language follows the
application language (English when none is set); a **character limit** is
respected by choosing the first rendering that fits, else `NEEDS_INPUT`
`ANSWER_TOO_LONG` — never a truncation; and every needs-input outcome carries a
stable reason code with a plain-language message that says where to fix it.

A deterministic suggestion is a copy of saved data, so `accept` re-resolves it
against the *current* data and refuses a stale one (`409 SUGGESTION_OUTDATED`).

## 6. AI suggestions

For everything else (`services/questions/service.py`):

```
ownership (404) → existing-answer confirmation → readiness (confirmed resume, language …)
  → deterministic context → AIProvider (structured output) → truth validation
  → SUGGESTED  |  NEEDS_INPUT  |  REJECTED (error)  |  FAILED (error)
```

- **Context** (`context.py`) is built on V0.8's `build_generation_context`: the
  confirmed resume's facts, the application's facts (`app.available_from`,
  `app.notice_period`, `app.work_authorization`), the target job (as context, never
  as a source) and the same fact ids. The AI is **not** shown the salary figure,
  contact details, notes, other applications, earlier answers or any earlier AI
  output — nothing generated can come back as fact. `input_hash` covers everything
  it sees.
- **Structured output** (`schemas/question_ai.py`), never free text:
  `{answer, answer_type, selected_options, source_fact_ids, job_fact_ids,
  confidence, needs_user_input}`. Contradictory output (an answer *and*
  `needs_user_input`, a choice without options, text *and* options) is invalid
  output and is rejected, not repaired.
- **Prompt** `application_answer_v1` (`app/ai/prompts/application_answer_v1.txt`):
  treats the context as data; "not finding something is not an answer"; numbers
  only if a cited fact states them; "No" only if a cited fact says so; options only
  from the list; no salary, no contact details; open questions answered
  conservatively from the facts or with `needs_user_input`. Versioned and
  fingerprinted like the V0.8 prompts (`PROMPT_FINGERPRINTS`, SHA-256 of the file).
- **Subjective questions** ("Why this role?") may be answered from the facts; the
  validator rejects invented company facts, culture, products, motivations,
  achievements and values (the V0.8 letter rules apply).
- **Regeneration** adds an attempt; the earlier attempt and its provenance stay. A
  regeneration that *needs input* clears the suggestion (the state mirrors the
  latest answered attempt); a rejected or failed one changes nothing.

## 7. Truth validation of an answer

`services/questions/validation.py` — reusing V0.8's claim machinery
(`documents.validation.ValidationRun`, `claims.py`); there is no second truth
framework. An answer that fails **any** check is rejected whole and never repaired;
the failure is stored on the attempt.

| Check | Rejects |
| --- | --- |
| structure | no cited fact (`MISSING_SOURCE`), a fact that doesn't exist (`UNKNOWN_FACT_ID`), a job fact used as proof about the candidate (`JOB_FACT_AS_SOURCE`) |
| claims (V0.8) | numbers / dates the cited facts don't state, skills / languages / degrees, tool-shaped tokens, organizations, leadership / seniority / credential / achievement claims, claims about the company or the writer's life, any pay talk |
| topic | the technology, tool or credential **named in the question** missing from the cited facts (`UNSUPPORTED_TOPIC`) — a true but unrelated fact cannot support "yes" or a number |
| choices | an option the question doesn't list (matched ignoring case, stored as listed), a repeated option, several options for a single choice, free text for a choice (`INVALID_OPTION`); a selected option is itself a claim and must be supported |
| numbers & dates | a non-number for a numeric question, an invalid date, a value no cited fact states (`UNSUPPORTED_NUMBER`) — "Worked extensively with Java" never becomes "5" |
| yes / no | "No" without a cited fact that says so (`UNSUPPORTED_NEGATIVE`) — **not found is not "No"** |
| length | longer than `max_length` (`ANSWER_TOO_LONG`) — never truncated |
| language | clearly not the application language (`WRONG_LANGUAGE`) |

An honest `needs_user_input` is not a violation: it is the correct answer to an
unsupported factual question and is stored as outcome `NEEDS_INPUT`.

## 8. Provenance

Every attempt records: `method`, `prompt_version`, `prompt_hash` (SHA-256 of the
prompt file), `ai_provider` / `ai_model`, `resume_id` / `resume_profile_id` /
`resume_version_number`, `application_context_snapshot_id`, `input_hash`,
`question_fingerprint` (SHA-256 of text, type, options and limit — a suggestion for
a since-changed question is flagged `suggestion_outdated`), `max_length`, the cited
facts with their text, the validation report, and the generation timestamp
(`created_at`). A deterministic attempt records the same minus the AI fields and the
resume (unless contact details or experience were used). The answer carries a
copy as `ai_provenance`, and `GET .../generations` returns the full audit trail,
also for rejected and failed attempts. Prompts, rejected output and provider
errors never appear in a response or a log.

V0.9 uses V0.8's conventions: the same status vocabulary for attempts, the same
`SourceFact` / `ValidationReport` / `Violation` models, the same error mapping, the
same fact ids.

## 9. Human Control in the question workflow

| User action | Endpoint | Effect |
| --- | --- | --- |
| Generate / Regenerate | `POST …/generate`, `…/regenerate` | new *suggestion* only; acknowledgement needed when an answer exists |
| Accept | `POST …/accept` | final answer := the server-held suggestion; `ACCEPTED` |
| Edit | `POST …/manual` with `based_on_suggestion: true` | final answer := the user's text; `USER_EDITED` / `HYBRID`; the suggestion is untouched |
| Answer manually | `POST …/manual` | final answer := the user's text; `USER_EDITED` / `USER`; no AI involved |
| Reject | `POST …/reject` | suggestion dismissed; the answer, if any, stays |
| Clear | `POST …/clear` | the user's answer removed; suggestion and history stay |
| Copy | (client) | the exact final answer to the clipboard — to use anywhere, with no automation |

## 10. Humanize

**✨ Humanize** rewords a validated Anschreiben so that it sounds more natural —
sentence rhythm and structure, generic or cliché-heavy phrasing, repetition,
transitions, excessive formality — **without changing what it says**.

```
existing letter version → Humanize AI → structured output → truth validation → NEW validated version
                                                              (rejects, never repairs; the letter is untouched on failure)
```

Humanize is **not a validator bypass**; it runs the V0.8 generation pipeline
(`GENERATING` row → AI → validation → `VALIDATED` / `REJECTED` / `FAILED`, one in
flight per application and type) with `services/documents/humanize.py`:

- Input is an **already validated** version (the current one, or a chosen
  `base_document_id`) and the facts *stored with it* — so it works after the resume
  was edited or deleted. The AI sees the letter body, the facts it cites, the job's
  title and company — not the salary sentence, the sender or anything else.
- Output (`AIHumanizedLetter`): the same paragraphs, roles and order, reworded, each
  citing facts.
- It goes through `ValidationRun` exactly like a generated letter (every claim in
  the reworded text must be supported by the facts the paragraph cites) **plus**
  the rewording rules: same structure; only facts the original paragraph cited
  (`FOREIGN_FACT`); a cited paragraph still cites (`MISSING_SOURCE`); nothing the
  original claimed disappears — its numbers, taxonomy terms, tool-shaped tokens and
  organizations are still there (`FACT_DROPPED`) and the position and company are
  still named where the original named them (`IDENTITY_NOT_PRESERVED`); same
  language (`WRONG_LANGUAGE`); something actually changed (`UNCHANGED`); no pay
  talk.
- The **server** carries over everything that is not prose: sender, recipient,
  subject, salutation, **the salary sentence**, closing formula, signature, each
  paragraph's `source_fact_ids` / `job_fact_ids`. The AI cannot change the target
  company, role or salary because it has no field for them.
- Result: a **new version** (`origin: humanized`, `base_document_id` = the version
  it came from, `input_hash` of the base — so it is "outdated" exactly when its base
  is). Cover Letter v1 → Humanize → v2; v1 stays readable and unchanged.
- Failure: `DOCUMENT_REJECTED` (with the reasons) or an AI error; a `REJECTED` /
  `FAILED` attempt is kept in the history, **nothing** becomes a document, the
  current letter stays current, and the user can retry.
- A letter the user edited can be humanized too: the user's words may stay (they
  are theirs, and support themselves) but the AI may not add to them either. Since
  that makes the new version current, it needs `acknowledge_edited_current` (§1).
- Prompt `cover_letter_humanize_v1`: input is a validated letter; preserve every
  fact, the salary sentence (not shown), and the position and company; no new facts,
  nothing removed that matters, no meaning change; keep the language; return the same
  paragraphs with their `source_fact_ids`. Fingerprinted (`PROMPT_FINGERPRINTS`),
  and its version and hash are stored on the version (`prompt_version`,
  `prompt_hash`).

The UI shows a confirmation ("Humanize this cover letter while preserving all
factual content?") that says it creates a new version and keeps the current one.

## 11. Editing, copying and the CV photo

**Edits are versions.** `POST /applications/{id}/documents/{document_id}/edit` saves
the user's edited text as a *new* version (`origin: user_edited`, `base_document_id`
= the version it started from; the base is never changed). No AI is involved, and
the version is marked `validation_status: user_authored`: the user's own words are
never presented as checked. Editable: a CV's summary, bullets and contact header; a
letter's subject, salutation, paragraphs, salary sentence, closing formula and
signature. Employers, titles, dates, degrees and skills are the resume's — change
them in the resume review. Text the user changed is stored with `origin: "user"` and
cites no facts; untouched text keeps its sources. An edit that changes nothing is
refused (`409 NO_CHANGES`) rather than saved as a duplicate. Versions stay immutable
(a saved version is write-once); each save is a new version.

**Regeneration safety.** A generation or Humanize while the current version is the
user's edit needs `acknowledge_edited_current` (§1). The edited version is never
deleted or changed.

**Copy / download.** The UI copies a document (or a question's final answer) as plain
text and offers a `.txt` download, so everything can be used outside JobPilot. No
PDF / DOCX export subsystem was built; the architecture does not prevent one (a
version's content is structured and immutable).

**The CV photo** is a user-owned asset (`application_photos`, one per application;
the file in the configured `FileStorage`):

- `PUT /applications/{id}/cv-photo` (multipart; JPEG or PNG by the file's own
  signature, ≤ `MAX_PHOTO_SIZE_MB`, default 2) adds or replaces it, `GET` returns the
  file exactly as uploaded (`Cache-Control: private, no-store`), `DELETE` removes it.
- It is stored beside the application, never inside a generated document; no
  document schema has a field for it, so no AI operation can read, write, describe or
  replace it. Regenerating, humanizing and editing leave it alone. The CV viewer shows
  it when present. The UI never presents it as AI output.
- Not in V0.9: cropping, resizing or any image editing; the photo is not yet part of
  an exported document (there is no export).

## 12. API

All endpoints require authentication and are scoped to the current user.

```
POST   /applications/{id}/questions                     add a question
GET    /applications/{id}/questions                     the questions with their answers + whether AI suggestions are available
GET    /applications/{id}/questions/{qid}               one question
PATCH  /applications/{id}/questions/{qid}               edit the question (refused if the user's answer would no longer fit)
DELETE /applications/{id}/questions/{qid}
GET    /applications/{id}/questions/{qid}/generations   every attempt (also rejected / failed): the audit trail

POST   /applications/{id}/questions/{qid}/generate      first suggestion (409 ALREADY_GENERATED if one exists)
POST   /applications/{id}/questions/{qid}/regenerate    a new suggestion
POST   /applications/{id}/questions/{qid}/accept        {replace_existing?}  accept the server-held suggestion
POST   /applications/{id}/questions/{qid}/reject
POST   /applications/{id}/questions/{qid}/manual        {answer | selected_options, based_on_suggestion?}
POST   /applications/{id}/questions/{qid}/clear

POST   /applications/{id}/documents/cover-letter/humanize   {base_document_id?, acknowledge_edited_current?}
POST   /applications/{id}/documents/{document_id}/edit      the user's edited text → a new version
POST   /applications/{id}/documents/cv | cover-letter       (V0.8) now accept {acknowledge_edited_current?}
PUT    /applications/{id}/cv-photo      GET /…/cv-photo      DELETE /…/cv-photo
```

Request bodies reject unknown keys: `user_id`, `application_id`, normalized text, hash,
status, suggestion, validation result and provenance cannot be set by a client.
`GET …/documents` additionally returns `photo`; document summaries gain `origin`,
`base_document_id` and `prompt_hash`, and `validation_status` may be
`user_authored`.

Notable errors: `ANSWER_REJECTED` (502, with `violations`), `AI_*` (as V0.8),
`RESUME_MISSING` / `RESUME_NOT_CONFIRMED` / `LANGUAGE_MISSING` … (409, with
`blockers`; only for the AI path), `EXISTING_ANSWER_CONFIRMATION_REQUIRED`,
`ANSWER_WOULD_BE_REPLACED`, `ANSWER_CONFLICT`, `SUGGESTION_OUTDATED`, `NO_SUGGESTION`
(409), `ANSWER_TOO_LONG`, `INVALID_OPTION`, `INVALID_NUMBER`, `INVALID_ANSWER` (422),
`QUESTION_EXISTS` (409), `NO_COVER_LETTER`, `NO_CHANGES`,
`EDITED_DOCUMENT_CONFIRMATION_REQUIRED` (409), `DOCUMENT_REJECTED` (502, Humanize).

V0.9's enums for questions use upper-case values (as in the specification:
`FREE_TEXT`, `NEEDS_INPUT` …); V0.8's document enums keep their lower-case values.

## 13. Authorization and security

`current_user → owns Application → owns UserJob` on every operation; another user's
application, question, attempt, document or photo and an unknown id are the same
`404`. Composite foreign keys make cross-user rows impossible: questions and photos
`(application_id, user_id) → applications`, answers and attempts `(question_id,
user_id) → application_questions`. A client cannot inject source fact ids (they are
produced and validated server-side), cannot mark an unvalidated suggestion as
accepted (`accept` takes no content), and cannot set validation or provenance.
Prompts, credentials and rejected output are never returned or logged. The AI is
never shown the salary figure, contact details, notes or earlier AI output. Photos
are sniffed by signature, size-limited and served only to their owner.

## 14. Frontend

`/applications/[id]` gains **Application questions** (`ApplicationQuestions`,
`QuestionCard`, `AnswerEditor`, `QuestionForm`; `lib/api/questions.ts`,
`lib/questions.ts`, `types/questions.ts`) and a section navigation (Job · Match ·
Preparation · CV · Anschreiben · Questions · Notes). It is a workspace, not a chat:
each question shows the employer's text, a status, and two clearly separate
areas —

```
┌ Why are you interested in this role?  ───────────────────────────────┐
│ AI suggestion          (or "Suggestion from your application data")  │
│   …text…   Sources (2) · checked against your resume · prompt vN     │
│   [Accept] [Edit] [Regenerate] [Reject]                              │
│ Your answer                                                          │
│   …text…   [Edit] [Copy] [Clear]    — or —  [Answer manually]        │
│   142 of 500 characters remaining                                    │
└──────────────────────────────────────────────────────────────────────┘
```

The user's answer is never hidden behind AI state. Confirmations (inline, not
`window.confirm`) guard regenerating over an answer, replacing it, clearing it and
deleting a question; the character counter turns into "N characters over the limit …
JobPilot never cuts it for you" and disables Save. The **Documents** section gains
**✨ Humanize** (with its confirmation), **Edit** (`DocumentEditor`), **Copy**,
**Download .txt**, a confirmation before regenerating over the user's edit,
origin-aware status and history ("edited by you", "reworded by Humanize"), "your
wording" markers on the user's text, and the **CV photo** control (add / replace /
remove; shown on the CV).

## 15. Database and migration

Migration `a9d4f2b7c1e3` (`v0_9_application_questions`), purely additive: six enum
types; the tables `application_questions`, `application_question_answers`,
`application_question_generations`, `application_photos`; and three columns on
`generated_documents` — `prompt_hash` (nullable), `origin` (default `AI`),
`base_document_id` (nullable, self-FK) — with `ck_generated_documents_base_iff_derived`
(a humanized or edited version names its base, an AI version never does; Alembic
does not detect check constraints, so it is created explicitly). Existing documents
simply become `origin = 'AI'`. `base_document_id` is deliberately `NO ACTION` (a
`SET NULL` would violate that check; deleting an application still removes the whole
family at once). `downgrade()` drops exactly what was added; V0.8 data comes back
exactly. Verified: upgrade → downgrade → upgrade → `alembic check`, with realistic
V0.8 data (`tests/test_migrations.py`).

## 16. Testing

No test calls a real AI. `FakeQuestionAI` / `FakeHumanizeAI`
(`tests/question_helpers.py`, `tests/humanize_helpers.py`) answer from the very
context they are shown — faithfully by default, unfaithfully on purpose.

| Area | Where |
| --- | --- |
| classification, every deterministic answer and needs-input case, numbers / dates / options / yes-no / length validation, prompt pinning | `tests/test_question_logic.py` |
| CRUD, ownership, deterministic path (no AI call), AI path, provenance, rejection, failures, Human Control (accept / edit / reject / manual / clear / regenerate), limits, DB constraints | `tests/test_questions_api.py` |
| Humanize (new version, original untouched, every kind of unsupported change rejected, salary / company / role preserved), user edits, regeneration safety, photo | `tests/test_humanize_api.py` |
| migration roundtrip with V0.8 data | `tests/test_migrations.py` |
| questions UI, documents UI (Humanize, edit, copy, photo, confirmations) | `frontend/__tests__/ApplicationQuestions.test.tsx`, `DocumentActions.test.tsx`, `documentText.test.ts` |
| real Chromium flow | `frontend/e2e/v09-application-questions.e2e.ts` |

Results at V0.9: backend **1973 passed, 1 skipped** (V0.8: 1748 passed, 1 skipped; +225
tests in the three files above and the migration tests); frontend **336 passed** (V0.8:
269); Playwright **6 passed** (5 earlier specs + V0.9); `ruff check app tests`, `mypy
app`, TypeScript, `next lint` and the production build clean; Alembic upgrade →
downgrade → upgrade → `alembic check` clean.

## 17. Limitations

- **Validation is lexical** (as in V0.8). It catches invented numbers, tools,
  organizations, credentials and titles, and the topic check ties a yes / number to
  the technology the question names, but it cannot prove an answer is *semantically*
  right: a fact that is about the right technology but doesn't say what the answer
  claims can pass. Conservative direction on purpose: a rejection costs a retry.
- Humanize's "nothing dropped" check covers numbers, taxonomy terms, tool-shaped
  tokens and organizations; a softened claim without such a word can pass.
- Deterministic recognition is pattern-based (English and German) and covers common
  labels; other phrasings go down the AI path, which is shown none of the structured
  data and so says "needs your input".
- AI suggestions flag a changed *question* as outdated, not a changed resume (the
  deterministic ones are re-checked against the current data on accept).
- Only German and English; user answers are not checked (they are the user's).
- Questions are entered by hand; no import. Photos are stored as uploaded: no
  cropping, no export, no use outside the in-app CV view.

## 18. Scope and what's deferred

| Deferred to | Feature |
| --- | --- |
| V0.10 (built) | browser automation, discovering questions from application forms (`source = "browser"`, `external_key`, `field_name`, `source_metadata` — filled as designed, no schema change to this model), filling fields, user takeover in the browser: see [`browser-automation.md`](browser-automation.md) |
| V0.11 | review and submission |
| V0.12 | the application lifecycle and tracking |
| later | PDF / DOCX export, a rich document editor, image editing, importing questions |
