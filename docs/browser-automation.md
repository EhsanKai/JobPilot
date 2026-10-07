# V0.10 — Browser Automation & Human-Controlled Application Filling

> **JobPilot fills forms; the user remains in control.**

V0.10 answers *"Open the employer's application form, work out what it asks, and fill
in what I have approved — without taking the application out of my hands."* From an
Application the user explicitly starts a **browser assistant**: JobPilot opens the
application's own page in a controlled Chromium, reads the form, maps each field to an
`ApplicationQuestion` (V0.9), fills only the answers the user has approved, reads every
value back to check it, and hands the browser to the user whenever they want it.

**V0.10 never submits an application.** There is no `SUBMITTED` status, no endpoint that
submits, and the browser interface has no way to click a button (§11).

> **V0.11 update.** V0.11 adds *Review & Submit*: a separate, explicitly authorized,
> exactly-once submission capability with its own guard, documented in
> [§24 — the Submission Safety Model](#24-v011-review--submit-the-submission-safety-model).
> Everything in §1–§22 still holds: the fill assistant and its `BrowserPage` interface
> still cannot press anything, and `SUBMIT_ACTIONS_ALLOWED` is still `False`.

```
Application
    ↓  (the user presses "Start browser assistant")
AutomationSession ── BrowserSession ── BrowserPage          (Chromium, Playwright inside only)
    ↓
FormDiscovery ──► DiscoveredField[]       neutral, serializable metadata
    ↓
FieldMapper   ──► ApplicationQuestion     (V0.9: same table, source = "browser")
    ↓
AnswerResolver ─► approved value | needs input | suggestion for review
    ↓
BrowserFiller ──► write ► read back ► verify ► FILLED | FILL_FAILED | USER_MODIFIED
    ↓
USER TAKES CONTROL ── edits anything ── resumes or finishes by hand
    ↓
STOP    (the user submits the application themselves)
```

## Contents

1. [Principles](#1-principles)
2. [Scope](#2-scope)
3. [Architecture](#3-architecture)
4. [Sessions and their lifecycle](#4-sessions-and-their-lifecycle)
5. [Form discovery and field identity](#5-form-discovery-and-field-identity)
6. [Questions and mapping](#6-questions-and-mapping)
7. [What may be filled: the approval hierarchy](#7-what-may-be-filled-the-approval-hierarchy)
8. [Filling and verification](#8-filling-and-verification)
9. [Human takeover](#9-human-takeover)
10. [Documents](#10-documents)
11. [The no-submit rule](#11-the-no-submit-rule)
12. [Legal and consent fields](#12-legal-and-consent-fields)
13. [Security challenges, login and unexpected websites](#13-security-challenges-login-and-unexpected-websites)
14. [Security and privacy model](#14-security-and-privacy-model)
15. [What the user sees](#15-what-the-user-sees)
16. [API](#16-api)
17. [Frontend](#17-frontend)
18. [Configuration](#18-configuration)
19. [Database and migration](#19-database-and-migration)
20. [Testing](#20-testing)
21. [Limitations](#21-limitations)
22. [Technical debt](#22-technical-debt)
23. [Preparing V0.11 (built — see §24)](#23-preparing-v011-built--see-24)
24. [V0.11 Review & Submit: the Submission Safety Model](#24-v011-review--submit-the-submission-safety-model)

## 1. Principles

**Human control at every stage** (the V0.9 principle, now on a live web page). The
assistant is a helper, never the owner of the application. The user can at any moment

- **pause** it — nothing is changed while it is paused;
- **take control** — the browser is theirs; the assistant writes nothing until they resume it;
- **stop** it — the assistant never touches the page again;
- edit any field, change any answer, choose another document, and finish by hand.

The user is never trapped in the workflow and never depends on the assistant to finish.
The assistant is **deterministic and constrained**, not an autonomous web agent: AI is used
for one thing only — matching an ambiguous field to an *existing* question — and its answer
is structured, confidence-gated and verified (§6).

## 2. Scope

**In V0.10:** browser sessions; opening the application page; discovering fields (text,
email, phone, url, number, date, textarea, select, radio group, checkbox, checkbox group,
file; ARIA widgets are *found*, not driven); mapping to questions; answer resolution;
filling with read-back verification; pause / take control / resume / stop; document
upload (the user's CV, Anschreiben or resume file, after they choose it); the discovered
field review UI; an event log.

**Out of scope, deliberately:** submitting or pressing *Submit / Apply / Send / Finish* (or
any button); solving, bypassing or working around CAPTCHAs, Cloudflare or other security
challenges; stealth fingerprints, proxy rotation, rate-limit bypass; storing or typing
credentials, or getting around a login; crawling the employer's site; following links;
ticking consent or legal checkboxes; LinkedIn-specific anti-bot handling; a remote-desktop
product.

## 3. Architecture

```
backend/app/
  models/automation.py            AutomationSession · AutomationField · AutomationEvent
  schemas/automation.py           API schemas (no browser internals)
  schemas/automation_ai.py        AIFieldMapping (structured output of the mapping AI)
  api/v1/automation.py            endpoints
  services/automation/
    browser/base.py               BrowserDriver · BrowserSession · BrowserPage + plain data types
    browser/playwright_driver.py  the Playwright/Chromium implementation (the ONLY Playwright code)
    browser/scripts.py            the JavaScript run inside the page (reads; guarded writes)
    discovery.py                  FormDiscovery: PageSnapshot → DiscoveredField[]
    mapping.py                    FieldMapper: field → ApplicationQuestion (+ AI, confidence-gated)
    resolver.py                   AnswerResolver: the approval hierarchy
    filler.py                     BrowserFiller: plan → decide → write → verify
    controller.py                 AutomationController: lifecycle, guards, fill orchestration
    documents.py                  which documents may be attached; DOCX rendering in memory
    safety.py                     URL / domain / submit-label / consent / challenge rules
    registry.py                   the live browsers of this server process
    service.py                    read models and the API-facing operations
    events.py · messages.py       the event vocabulary and the user's wording
```

- **No Playwright outside `browser/`.** The domain talks to three small interfaces
  (`BrowserDriver`, `BrowserSession`, `BrowserPage`) and plain dataclasses (`RawControl`,
  `PageSnapshot`, `FieldLocator`, `FieldValue`). Another engine could replace Chromium
  without touching discovery, mapping, resolution, filling or the controller.
- **Playwright runs on its own thread** with its own event loop; the domain calls it
  synchronously. That keeps the web server's loop free, works regardless of which loop
  serves a request, and keeps the automation logic plain, testable synchronous code.
- **The page is read by one script and written by five operations** (type, choose, check,
  attach — and read-back). Controls are found by what the page says about them (id, name,
  label), never by position.

## 4. Sessions and their lifecycle

`AutomationSession` (table `automation_sessions`): `id`, `application_id`, `user_id`,
`status`, `target_url`, `current_url`, `page_title`, `allowed_hosts`, `attention_code`,
`browser_mode`, `submit_detected`, `last_error_code` / `last_error`, `created_at`,
`updated_at`, `started_at`, `ended_at`. At most **one live session per application** (a
partial unique index), so two clicks cannot open two browsers.

```
STARTING ─► RUNNING ◄──► PAUSED
              │  ▲
              ▼  │
          USER_CONTROL            the user took the browser over
RUNNING ─► BLOCKED                a security check / login / refused access needs the user
RUNNING ◄─► COMPLETED             nothing left for the assistant (a final button was seen)
any live ─► STOPPED               the user stopped it (or the browser is gone)
STARTING / RUNNING ─► FAILED      the browser or the page failed
```

| Status | Meaning | The user can |
| --- | --- | --- |
| `STARTING` | the browser is being launched and the page opened | wait |
| `RUNNING` | the assistant may read and fill | pause · take control · fill · rediscover · stop |
| `PAUSED` | no further action is taken | resume · take control · stop |
| `USER_CONTROL` | the browser is the user's | resume · stop |
| `BLOCKED` | a CAPTCHA / login / refusal needs the user | take control · stop |
| `COMPLETED` | all approved answers are in and a final submission control exists | anything RUNNING allows |
| `STOPPED` / `FAILED` | ended | start again |

`SUBMITTED` does not exist. `resume` from `BLOCKED` is refused ("take control first"): the
user deals with the page, *then* hands it back. A session whose browser is gone (the server
restarted, the window was closed) is reconciled to `STOPPED` (`BROWSER_GONE`) the next time
it is looked at; the in-memory browser registry is process-local (§21).

**Starting.** `POST …/automation/start` takes **no URL**: the page is the
`application_url` of the application's context snapshot (V0.7) — the posting's apply URL,
or the posting page for sources that host the form. A client cannot supply or change it. It
is validated (http/https only, no embedded credentials, no private/loopback/link-local
address unless `AUTOMATION_ALLOW_PRIVATE_HOSTS`, including DNS that resolves privately) and
stored **without query or fragment**.

## 5. Form discovery and field identity

One script reads every visible control and a few safety signals (`PageSnapshot`).
`FormDiscovery` turns each control into a `DiscoveredField`: label, name, id, input type,
required, max length, options, form context, a `locator` description — neutral, serializable
metadata. Never raw HTML, never a Playwright locator, never a value.

**Identity** (`field_key`) is built from what the page says, strongest evidence first: a
**stable DOM id** → the input's **name** → the **normalized label**. Generated ids
(`:r1:`, `headlessui-…`, long numbers, UUIDs) are not trusted. A label change ("Email" →
"Email address") does not change a field's identity; neither does a new field appearing
above it. Equal identities in one page get `#2`, `#3`. No `nth-child`, no coordinates.

Detected: text, email, tel, url, number, date, textarea, select, radio groups, a single
checkbox, checkbox groups, file inputs; password fields only as a signal (never recorded).
Found but **not driven**: ARIA widgets (`role=combobox/listbox/radiogroup/switch/…`),
contenteditable, month/time/week/datetime-local, multi-selects, read-only/disabled fields →
`USER_ACTION_REQUIRED`. Search boxes, hidden inputs, honeypots (off-screen,
`display:none`) and buttons are ignored.

**Re-discovery** happens on demand (`POST …/discover`), after resuming, and — automatically
— between rounds of a fill: a follow-up that appears after an answer ("Please explain your
visa status") is discovered, mapped to a new question, and **filled only if it has an
approved answer**. No polling, no DOM observers.

## 6. Questions and mapping

A discovered field becomes (or maps to) a V0.9 `ApplicationQuestion` — no parallel question
system. Browser-created questions are neutral: `source = "browser"`,
`external_key = "browser:<field_key>"`, `field_name`, `source_metadata` (input type,
normalized label, name, id, form, option values, selector strategy, timestamp — small, no
page source). Files and legal confirmations are not questions.

**Deduplication.** `(application_id, external_key)` is unique, so rediscovering the same form
— in the same or a later session — reuses its questions and their answers. A label that
changed on the form updates a browser-created question's text, options, limit and type
*unless the user's existing answer would no longer fit*: then the question is left alone and
the field is flagged `NEEDS_REVIEW` (`FIELD_CHANGED`). A question the **user** wrote is never
rewritten.

**Mapping states:** `UNMAPPED` · `AUTO_MAPPED` · `USER_CONFIRMED` · `NEEDS_REVIEW`. Only
`AUTO_MAPPED` with `HIGH` confidence and `USER_CONFIRMED` may be filled. Rules, in order,
deterministic first:

1. **identity** — a question already made for this very field;
2. **exact text** — an existing, unclaimed, compatible question in the same words;
3. **topic** — the same V0.9 deterministic topic (email, phone, salary, start date, notice
   period, work authorization …), English or German;
4. **AI** — only if configured, only among *existing, unclaimed, compatible* questions, with
   structured output (`application_question_id`, `confidence`, `reason`); the id must be one
   of the candidates (an invented id is ignored); only `HIGH` that also passes the type check
   is applied, `MEDIUM`/`LOW` → `NEEDS_REVIEW` with a proposal; the prompt
   (`field_mapping_v1`, fingerprinted) shows only the field's label, type, options and limit
   plus the candidates — never an answer or any personal data; provenance (prompt version and
   hash, provider, model, confidence, reason) is stored on the field;
5. without AI, a merely **similar** question is proposed (`NEEDS_REVIEW`), never applied;
6. nothing fits → a **new** browser question is created.

An ambiguous match (two equally good questions) is `NEEDS_REVIEW` with no proposal. The
user can **confirm**, **change**, **leave unmapped** (never filled) or **add a question**;
their decision is final, applies to later sessions of the application, and a question left
with nothing on it is removed when its field is moved elsewhere. A field is mapped only to a
question of its own application.

## 7. What may be filled: the approval hierarchy

`AnswerResolver` decides, per question, in this order:

| # | Source | Filled? |
| --- | --- | --- |
| 1 | **the user's own answer** (V0.9 `USER` / `HYBRID` final answer) | yes — highest priority |
| 2 | **deterministic application data** — salary, availability, notice period, work authorization, sponsorship, application language, contact details, years of experience, the parts of the name (from the account) — re-read from the saved data *now* | yes |
| 3 | **an accepted answer** (V0.9 copies an accepted AI suggestion into the final answer) | yes |
| 4 | an unreviewed AI suggestion | **never** — shown for review |
| 5 | nothing | "Needs your input" — never invented, also when the field is required |

A deterministic suggestion the user *rejected* stays rejected. Open follow-ups ("Please
explain …") are never answered deterministically. **V0.9 validation stays authoritative:**
the resolver only consumes answers that already passed it and never re-judges truth; the
filler only checks that a value *fits the control* — an option the form really offers, a
number for a number field, a date in the control's format, text within the form's character
limit — and **never alters or truncates** a value (otherwise: `Needs your review` with the
reason).

## 8. Filling and verification

For each approved field: **plan** (what exactly goes into this control) → **decide** →
**write** → **read back** → **verify**.

- Text controls are typed with Playwright's `fill` (events fire as a React/Vue form
  expects); selects by option value; radios and checkboxes by checking the input; groups
  exactly (the listed options on, the rest untouched when empty); files from memory.
- **Decide** — write only when the field is empty and never written by the assistant, still
  holds exactly what the assistant wrote (the approved answer changed since), or the user
  asked for **that field** to be overwritten. Otherwise it is the user's: `USER_MODIFIED`.
  A field the user *cleared* is theirs too. The assistant remembers only a **salted hash**
  of what the browser showed after its write (`last_agent_value_hash`) — never the value.
- **Verify** — the browser's value must equal the approved one; if the page rewrote it
  (formatting, upper-casing, truncating) the field is `FILL_FAILED` (`VALUE_MISMATCH`) and
  the assistant does not loop.
- A field that is already correct is `FILLED` with no write (idempotent). Fill is safe to
  repeat; after a page reload, fields the user (or the reload) emptied are reported as
  `USER_MODIFIED` and refilled only on request.

## 9. Human takeover

- `take-control` / `pause` / `stop` only change the session's status, and **every write is
  gated on it**: immediately before writing, the session row is locked `FOR SHARE` and its
  status must be `RUNNING`. The three actions need the row exclusively, so **once one of them
  has returned, no further write can happen** (a write already in flight finishes first). A
  test interrupts a fill in flight and counts the writes.
- While paused or in the user's control, `fill` and `discover` are refused (`409`).
- `resume` (the UI sends `fill: true`) first **reads** the page, notes what the user
  changed (`USER_MODIFIED`), and then fills only what is still empty. It never refills
  blindly; a field the user edited keeps their value. "Fill again" on a changed field asks
  for confirmation first (`overwrite_field_ids` names exactly the fields to overwrite).
- `stop` leaves the browser open for manual use (it is closed after
  `AUTOMATION_IDLE_CLOSE_MINUTES` idle, or immediately with `close_browser`); the assistant
  never touches it again.

## 10. Documents

A file field proposes the **current version** of the document it clearly asks for (CV /
résumé / Lebenslauf → tailored CV; cover letter / Anschreiben / Motivationsschreiben →
cover letter; both words or neither → *not guessed*, "Other documents" gets no proposal).
Nothing is attached until the user presses **Use this file** (or **Choose another**); the
choice is pinned to **that immutable version**: a newer version never replaces it, and the UI
says when one exists. Choices are the validated versions of *this application's* CV and
Anschreiben (labelled with their version and origin, "edited by you", "reworded by
Humanize") and the resume file the application is built on. The client sends a document
**id and type**, never a path; the server re-resolves it against the user's application at
upload time, renders a generated document to a DOCX **in memory** (python-docx) or reads the
resume file through the storage abstraction, and passes the bytes to the browser. No path is
ever returned or logged. A form whose `accept` list excludes the format is left to the user
(`FILE_TYPE_NOT_ACCEPTED`); the attached file is read back (name and size). A file the user
replaced in the browser is theirs.

## 11. The no-submit rule

`SUBMIT_ACTIONS_ALLOWED = False` (`services/automation/safety.py`), enforced structurally
(still true in V0.11 for the fill assistant; submitting is a separate capability, §24):

1. **The browser interface has no click.** `BrowserPage` can read, type, choose, check and
   attach — there is no method that presses a button or a key, so no code built on it can
   submit. The Playwright implementation only resolves controls that discovery returned
   (never a button) and refuses any element that is a button/submit/image/reset.
2. **Final actions are recognized, never pressed** (`is_submission_label`: *Submit, Apply,
   Send application, Complete/Finish application, Bewerben, Bewerbung absenden/abschicken,
   Absenden …*). The user is told: *"The application appears ready for submission. V0.10
   does not submit applications. Please submit manually."* The assistant does not follow
   "Apply" links either: a page without a form says so (`NO_FORM_FOUND`).
3. **Tests prove it** against a real page whose *Submit application* button reports every
   click and submission: the whole flow runs with `click`, `dblclick`, `tap`,
   `dispatch_event` and key presses on Playwright's page, frame, locator and element handle
   replaced by tripwires, and the page's submission counter stays `0` (also in the
   Chromium E2E). A structural test asserts that the browser interface has no
   click, press or submit method at all.

## 12. Legal and consent fields

Terms, privacy policy, data-processing consent, declarations of truth, "I certify …",
newsletters / talent-pool opt-ins and signature fields (English and German) are marked
`USER_ACTION_REQUIRED` (`LEGAL_CONSENT`), get no question and are **never ticked** — not
automatically, and not even if the user wrote a matching question with the answer "Yes".
The UI says: *"Only you can give it: JobPilot never ticks these."* The user ticks them
themselves, and the assistant leaves them alone. (V0.11 adds an explicit, per-consent
authorization for exactly the consent text shown — §24.6; the fill assistant still never
ticks a consent.)

## 13. Security challenges, login and unexpected websites

- **CAPTCHA / security check** (reCAPTCHA, hCaptcha, Turnstile, Arkose, DataDome,
  PerimeterX, a Cloudflare-style interstitial): `BLOCKED`, `SECURITY_CHALLENGE`, the
  assistant does nothing else. The user takes control, completes it and resumes; a *widget on
  the form* is then acknowledged and not raised again, an *interstitial that gates the whole
  page* is never waved through.
- **Login** (a visible password field): `BLOCKED`, `LOGIN_REQUIRED` — *"Login required.
  Please take control of the browser and sign in manually."* Password fields are never
  discovered, stored or logged; the browser profile is empty and is discarded with the
  session.
- **Refused access** (HTTP 401/403/429 on the page): `BLOCKED`, `ACCESS_RESTRICTED`. Nothing
  tries to get around it.
- **Unexpected website:** the assistant starts on the application's site (its registrable
  domain, with the posting's host as the anchor). If the page moves to another site — at load
  or later — the session is `PAUSED` (`UNEXPECTED_DOMAIN`) and writes nothing until the user
  confirms *"This website is part of my application"* (or takes control). Navigation to
  private addresses is also blocked inside the browser.
- Errors never retry dangerous operations: a failed write is reported once.

## 14. Security and privacy model

| Concern | Rule |
| --- | --- |
| Ownership | `current_user → owns Application`; sessions, fields and events carry `user_id` and composite foreign keys; another user's session, field, document or question — or an unknown id — is the same `404`. |
| Client-supplied data | Request bodies forbid unknown keys. A client cannot send a URL, an owner, a status, a locator, a decision about another user's question or a file path. |
| Browser secrets | Cookies, tokens, passwords, browser process ids and connection URLs never leave the browser/server and are never in a response or log. The browser starts with an empty profile per session. |
| Stored URLs | Scheme, host and path only — never a query, fragment or credentials. |
| Form values | Not stored. Only a salted SHA-256 of the last written value (to tell "still mine" from "the user changed it"). |
| Events | Codes and counts only; no label text, value or URL query. |
| Logs | Field ids, types, statuses and error codes only. |
| Screenshots | Taken on request, returned `Cache-Control: private, no-store`, never written anywhere. |
| SSRF | Application URLs must be public http(s); private/loopback/link-local/odd-spelled IPs, intranet names and names that resolve privately are refused; the browser blocks private navigation. |
| Resource limits | `AUTOMATION_MAX_BROWSERS` live browsers per process; idle browsers are closed; `AUTOMATION_MAX_FIELDS` per page. |

## 15. What the user sees

**Browser visibility.** By default (`AUTOMATION_BROWSER_HEADLESS=false`) Chromium is a
**visible window on the machine that runs JobPilot** — the user watches the assistant work
and, after *Take control*, uses the very same window. On a server without a display
(headless) JobPilot says so: *"This server runs the browser without a window"*, offers a
**live view** (a screenshot on request) and a link to the application page to continue in
the user's own browser. It never pretends to offer control it cannot give. A remote-desktop
or streamed browser is not part of V0.10.

User-facing statuses: Starting · Running · Paused · User control · Needs your input · Needs
your review · Filled · You changed this · Needs you · Blocked · Stopped · Failed. The browser
panel shows the status, the current page, *Fields found / Filled / Needs review / Needs
input*, the controls, the field review table and an activity list.

## 16. API

```
POST   /applications/{id}/automation/start                        open the application page (no body)
GET    /applications/{id}/automation                              sessions + whether one can be started
GET    /applications/{id}/automation/{sid}                        the session (also refreshes it from its browser)
GET    /applications/{id}/automation/{sid}/fields                 discovered fields + mappable questions
GET    /applications/{id}/automation/{sid}/events                 the event log
GET    /applications/{id}/automation/{sid}/screenshot             PNG of the browser, never stored
POST   /applications/{id}/automation/{sid}/pause | take-control | stop {close_browser?}
POST   /applications/{id}/automation/{sid}/resume                 {fill?}
POST   /applications/{id}/automation/{sid}/confirm-domain         {host}
POST   /applications/{id}/automation/{sid}/discover               read the page again (reads only)
POST   /applications/{id}/automation/{sid}/fill                   {field_ids?, overwrite_field_ids?}
POST   /applications/{id}/automation/{sid}/suggest                AI suggestions for unanswered mapped questions (never accepted)
POST   /applications/{id}/automation/{sid}/fields/{fid}/mapping   {action: confirm|change|unmap|create, question_id?}
GET    /applications/{id}/automation/{sid}/fields/{fid}/documents the documents that can be attached
PUT    /applications/{id}/automation/{sid}/fields/{fid}/document  {document_type, document_id}  "Use this file"
DELETE /applications/{id}/automation/{sid}/fields/{fid}/document
```

`409` codes: `INVALID_SESSION_STATE` (with the action and status), `SESSION_ALREADY_ACTIVE`,
`NO_APPLICATION_URL` and the URL-safety codes, `CONFIRM_DOMAIN_FIRST`,
`DOMAIN_CHANGED_AGAIN`, `BROWSER_GONE`; `503 TOO_MANY_BROWSERS`. A browser that cannot start
or a page that cannot load is not an HTTP error: the session is returned as `FAILED` with
`last_error`.

## 17. Frontend

`/applications/[id]` gains **Browser assistant** (`BrowserAssistant`, `assistant/FieldRow`,
`assistant/DocumentPicker`, `assistant/LiveView`; `lib/api/automation.ts`,
`lib/automation.ts`, `types/automation.ts`) and a section-navigation entry. It polls the
session every 3 s while the assistant is live. Nothing is started, filled or attached
without a click; there is no submit control anywhere.

## 18. Configuration

| Variable | Default | |
| --- | --- | --- |
| `AUTOMATION_BROWSER_HEADLESS` | `false` | `false`: a visible window (local use). `true` for servers / CI. |
| `AUTOMATION_CHROMIUM_PATH` | — | A specific Chromium (otherwise Playwright's own). |
| `AUTOMATION_ALLOW_PRIVATE_HOSTS` | `false` | Allow localhost/private application URLs (development, tests). |
| `AUTOMATION_NAVIGATION_TIMEOUT_SECONDS` | `30` | |
| `AUTOMATION_IDLE_CLOSE_MINUTES` | `30` | An unused browser is closed. |
| `AUTOMATION_MAX_BROWSERS` | `3` | Live browsers per server process. |
| `AUTOMATION_MAX_FIELDS` | `300` | Controls read per page. |

Install the browser once: `playwright install chromium` (the backend depends on
`playwright`).

## 19. Database and migration

Migration `b4e8c2d6a1f9` (`v0_10_browser_automation`), purely additive: tables
`automation_sessions`, `automation_fields`, `automation_events`; enum types
`automation_status`, `automation_mapping_status`, `automation_fill_status`. Composite
foreign keys `(application_id, user_id)` and `(session_id, user_id)` make a row for someone
else's application or session impossible; `uq_automation_sessions_one_live` (partial)
allows one live session per application; `ck_automation_sessions_ended_iff_terminal`; a
field's `question_id` / `proposed_question_id` are plain `ON DELETE SET NULL` references (a
deleted question unmaps the field). `downgrade()` drops exactly what was added.
Verified: upgrade → downgrade → upgrade → `alembic check`, with realistic V0.9 data
(`tests/test_migrations.py`).

## 20. Testing

No test touches the internet or calls a real AI. A **real headless Chromium** drives a
local fake employer website (`backend/tests/fake_site.py`: every supported control, a
required field, a limited field, a field that rewrites input, a dynamic follow-up, two legal
confirmations, three file inputs, an instrumented submit button, a CAPTCHA page, a login
page, an interstitial, a 403, an empty page, an SPA, a cross-site redirect).
`tests/automation_helpers.Human` stands in for the person at the keyboard.

| Area | Where |
| --- | --- |
| URL / domain safety, final-action and consent recognition, field identity, mapping rules, planning and the fill decision | `tests/test_automation_logic.py` |
| sessions: start rules, lifecycle, invalid transitions, ownership, no secrets, challenge / login / forbidden / unexpected domain, failures, interrupting a fill | `tests/test_automation_sessions.py` |
| discovery, questions and deduplication, mapping (incl. AI), approval hierarchy, every control, verification, user modifications, resume, legal fields, no submit | `tests/test_automation_fields.py` |
| documents: proposal, "Use this file", pinned versions, ownership, formats | `tests/test_automation_documents.py` |
| migration roundtrip with V0.9 data | `tests/test_migrations.py` |
| UI | `frontend/__tests__/BrowserAssistant.test.tsx` |
| real Chromium end to end | `frontend/e2e/v10-browser-assistant.e2e.ts` |

## 21. Limitations

- **Native controls only.** ARIA/custom widgets and contenteditable are found but left to
  the user; multi-selects, month/time/week inputs likewise. Shadow DOM and iframes are not
  read (so an embedded application form in an iframe is not seen).
- **One server process.** The browsers live in process memory; several workers need sticky
  routing. A restart ends sessions (`BROWSER_GONE`); nothing is lost but the open window.
- **DOCX only.** Generated documents are attached as DOCX (no PDF export exists yet); a
  PDF-only field is left to the user, or can take the user's own uploaded PDF resume.
- **No link following.** The assistant never presses "Apply" or follows links, so a posting
  page that only *links* to the form needs the user to open the form first.
- **Heuristic recognition.** Final-action, consent, challenge and document-type detection
  are word- and marker-based (English and German); an unrecognized wording is treated
  conservatively (filled only if approved) rather than guessed. Same-site detection uses the
  last two labels (three for `co.uk`-style names) without a public-suffix list.
- **Re-discovery is on demand** (and between fill rounds); there is no observer. A page
  reload empties fields the assistant filled; they show as "You changed this" and are
  refilled on request.
- **Live view** is a screenshot, not a streamed, clickable browser.
- **Verification is value equality.** A page that normalizes input (a phone formatter) is a
  `FILL_FAILED` rather than an accepted transformation.

## 22. Technical debt

- `controller.py` is long: lifecycle, inspection, mapping orchestration and the fill loop
  share one module; splitting (e.g. `inspection.py`, `fill_pass.py`) is natural once V0.11
  adds review.
- A field's `question_id` has no composite ownership key (PostgreSQL cannot null one column
  of a composite key through SQLAlchemy 2.0's `ON DELETE`); ownership of the mapping is
  enforced in the service and tested.
- The registry is an in-process singleton; a pluggable session store would be needed for
  multi-process or remote browsers.
- Locator resolution re-enumerates the page's controls for every read/write (simple and
  robust, O(fields) per write).

## 23. Preparing V0.11 (built — see §24)

V0.11 (review and submission) can build on the pieces that exist: the event log
(`FIELD_FILLED`, `FIELD_VERIFIED`, `USER_MODIFIED` …) is the audit trail; `submit_detected`
and the recognized final controls are what a *reviewed* submission would act on — through a
new, separate, explicitly authorized capability behind the human's confirmation (a
"final review" step showing every field, its source and its verified value), **not** by
loosening `SUBMIT_ACTIONS_ALLOWED`. Consent authorization would be per-consent and explicit.
`AutomationField.last_value_source` and the read-back values give the review its
provenance; a PDF export (V0.8 content is structured) would remove the DOCX-only limit.

## 24. V0.11 Review & Submit: the Submission Safety Model

> **AI prepares the application; the user reviews it and authorizes it; only then does
> JobPilot press the employer's final button — once.**

V0.11 is the first version that may submit an application. It never does so because the
form is complete, because the page shows a final button, because answers were approved
earlier or because the application "looks ready". It submits only after a **final review**
the user confirmed, an **explicit, per-consent authorization** of every consent that is
given, and a **fresh, explicit submission authorization** given immediately before — and it
re-proves, immediately before the press, that the page is still exactly what was reviewed.

```
Discover → Map → Review → Approve → Fill → Verify → Human takeover            (V0.10)
   → Final Review              FinalReview: immutable snapshot + SHA-256 of the live state
   → Consent Review            ConsentAuthorization per consent, for its exact text
   → Explicit Authorization    SubmissionAuthorization: review + hash + session + host, expiring
   → Dedicated Submit          FinalSubmissionChannel.submit_final_application(SubmitPermit)
   → Submit Exactly Once       authorization consumed + attempt created in one locked transaction
   → Verify Result             SUCCESS on evidence · FAILED_BEFORE_SUBMIT · SUBMISSION_UNKNOWN
```

### 24.1 Why submit is separate from fill

Filling and submitting have different risks. A wrong fill is visible and reversible; a
submission is not. So V0.11 does **not** loosen V0.10:

- `SUBMIT_ACTIONS_ALLOWED` stays `False` — it describes the fill assistant, which still never
  submits; the V0.10 test pinning it is unchanged.
- `BrowserPage` / `BrowserSession` / `BrowserDriver` are unchanged and still have no click,
  key press or submit method (the V0.10 structural test still passes, unmodified).
- Pressing the final button lives on a **second, narrow interface**,
  `FinalSubmissionChannel` (`browser/final_submit.py`), which a Playwright session offers in
  addition (`SupportsFinalSubmission`):

  | Method | Does |
  | --- | --- |
  | `find_final_controls()` | lists visible buttons (reads) |
  | `page_marker()` | which page *instance* is shown — changes on any navigation, reload or new window |
  | `submit_final_application(permit)` | presses the one button the permit names, once |
  | `read_outcome()` | what the page shows afterwards (reads; kept in memory only) |

  There is no `click(selector)`, no key press and no script evaluation. The acting method
  takes a `SubmitPermit` — no selector, no URL. A permit can only be created through
  `issue_permit`, whose only caller is `services/automation/submission.py` (a test pins
  that), and is **single-use**: redeeming it twice raises.
- The Playwright implementation (`PlaywrightFinalSubmission`) re-finds the button from the
  permit's description (by stable id, else name + label + ordinal — never position alone),
  checks the page's host equals the permit's, that the element is a real `<button>` or
  `<input type=submit>`, enabled, and able to receive the press (a Playwright *trial* click,
  which presses nothing). Any of these failing raises `SubmissionNotTriggered` — certainly
  not pressed. Then exactly one `click()`. It is the only `.click(` in `app/` (a test pins
  that too).

### 24.2 The final review

`GET …/automation/{sid}/final-review` computes the **submission state** from the live page
(`services/automation/review.py`), after bringing the session's fields up to date (a V0.10
discovery — reads only). It shows, and the UI renders in six sections:

1. **Destination** — company, role, website (host and site), the page (no query), the source,
   the final button's caption.
2. **Personal information** and 3. **Application questions** — for every field that will
   be submitted: its identity, label, question, **final value as the form holds it**, its
   **source** (`USER` "Your answer" / "Typed by you in the browser", `DETERMINISTIC` "From
   your application data", `ACCEPTED_AI` "AI-generated → accepted by you",
   `BROWSER_DISCOVERED` "Already on the page"), approval and verification (`VERIFIED` = the
   page holds exactly the approved value; read back, not assumed).
4. **Documents** — per file field: document id, type, exact version, origin ("edited by
   you", "reworded by Humanize" …), a SHA-256 of the version's content, and the attached
   file as the page reports it. The pinned version is never replaced by a newer one; a
   newer version is a *note*, choosing it is a change.
5. **Consents** — each one separately, with its exact text, required or not, given in the
   page or not, authorized by the user or not.
6. **Warnings** — *blockers* (anything unresolved) and notes.

**Blockers** (anything listed blocks review, authorization and submission): the assistant
not working on the page (user in control, paused, blocked, browser gone), another website,
a security check, a login, a truncated or changed form, no final button / **more than one
plausible final button** / a final action that is a link, a field that is gone, a mapping
that needs review, an unreviewed AI suggestion, a field without an approved answer
(answer it, type it, or leave it unmapped to send it empty), an empty required field, an
approved answer not yet (or no longer) in the form, a failed fill, a control JobPilot cannot
read, a chosen document that is unavailable or not attached, a missing required file, a
consent not authorized / given without authorization / authorized but not given / whose text
changed, an earlier attempt still running, an application already submitted, or an earlier
**unknown** result the user has not checked yet.

Final-button identification uses captions as *candidate signals* only (English and German:
*Submit application, Send application, Complete application, Apply, Bewerbung absenden /
einreichen / abschicken, Jetzt bewerben …*); `type=submit` alone is none, and two plausible
candidates block.

**Confirming** (`POST …/final-review {expected_state_hash}`) persists a `FinalReview` only
if the live state still has exactly the hash the user was shown and nothing blocks it.

### 24.3 Immutable reviewed state

`FinalReview` (`automation_final_reviews`) is write-once (`ImmutableMixin`): the snapshot
(the canonical state, including the values the user reviewed), `state_hash`,
`schema_version`, destination host / site / URL, the reviewed `page_marker`, and the time.
Only its lifecycle moves, forward: `ACTIVE → INVALIDATED` (with reason) / `SUPERSEDED` (a
newer review) / `USED` (its authorization was consumed). One `ACTIVE` review per session
(partial unique index). It is never edited to match a changed page: the user reviews again.

### 24.4 The state hash

`hash_state` = SHA-256 of the canonical JSON (sorted keys, no whitespace) of the state:
application and session, destination host / site / URL, every field (key, label, type,
required, mapping status, question id and text, final value, approved value, source,
approval, verification), every document (key, type, id, version, origin, content hash,
attached file), every consent (key, exact text and its hash, required, state, authorized)
and the final button (caption, tag, type, id, name, form, ordinal). **Not** hashed: row ids
that do not change what is sent, timestamps, display labels, screenshots, UI state. Which
page *instance* was reviewed is compared separately (`page_marker`), so a reload — which may
look identical — still counts as a change. Tests pin: identical state → identical hash; each
meaningful change → a different hash; volatile metadata → the same hash.

### 24.5 Submission authorization

`POST …/submission-authorization {final_review_id, reviewed_state_hash, confirm: true}` —
`confirm` must be literally `true` ("I have reviewed the information above and authorize
JobPilot to submit this application"). It is granted only when, **now**: the user owns the
application and session; the session is working on the page with a live browser; the review
is the session's `ACTIVE` one and the request carries its hash; the live state has no blocker
(this covers domain, CAPTCHA, login, required fields, answers, mappings, documents, consents,
history); the live hash equals the review's and the page instance is the reviewed one; and
no other authorization is open. `SubmissionAuthorization` records user, application, session,
review, hash, host, `AUTHORIZED` → `CONSUMED` / `EXPIRED` / `INVALIDATED`, `expires_at`
(`SUBMISSION_AUTHORIZATION_TTL_SECONDS`, default 10 min), `consumed_at`, `invalidated_at` +
reason. One open per application, one per review (database constraints). The UI asks for it
with a deliberate step — a checkbox naming the website, then **Authorize & Submit** — not a
"continue" in the workflow; the server never trusts the UI for any of it.

### 24.6 Consent authorization

Each legal confirmation (V0.10's `LEGAL_CONSENT` fields) is represented on its own, with its
exact text (`automation_fields.consent_text`: the label, plus the options of a group).
`POST …/consents/{field_id} {consent_text_hash, give}` records a `ConsentAuthorization` for
**exactly** the text the user was shown (refused with `CONSENT_TEXT_CHANGED` if the page now
says something else); with `give` (default) JobPilot then ticks *that one* checkbox, under the
same write gate as every fill, and reads it back. `DELETE` withdraws it (and unticks the box).
There is no "agree to everything", answering "Yes" to a question authorizes nothing, and the
fill assistant still never ticks a consent. Required consents must be authorized and given; a
consent ticked in the page without authorization blocks; a changed text matches no
authorization and invalidates the review.

### 24.7 Exactly-once protection

`POST …/{sid}/submit {authorization_id}` — the request names an authorization and nothing
else (unknown keys: 422; no URL, selector or form data can be sent):

1. **Claim** (one transaction): lock the session row, lock the authorization row; it must be
   the user's, this application's and this session's, `AUTHORIZED`, unexpired, its review
   still `ACTIVE`, the application not submitted / in flight / unknown, the session working.
   Then `AUTHORIZED → CONSUMED`, review `→ USED`, and a `SubmissionAttempt` row — committed
   together. A concurrent second request waits for the lock and then finds `CONSUMED` (409).
   Backstops in the database: one attempt per authorization; one unfinished attempt per
   application.
2. **Verify under the write gate**: the session row is locked `FOR SHARE` (a take-control,
   pause or stop waits until the press is over — and if one came first, nothing is pressed),
   the state is recomputed from the live page without writing anything, and must have no
   blocker, the reviewed page instance, the authorized host and the reviewed hash.
   Otherwise: `FAILED_BEFORE_SUBMIT` (`STATE_CHANGED`, `PAGE_RELOADED`, `WRONG_DESTINATION`,
   `SUBMISSION_BLOCKED`, `USER_TOOK_CONTROL` …) — nothing pressed.
3. **Record, then press**: `submit_triggered_at` is written and committed (in its own short
   transaction) *before* the press; then the permit is issued and redeemed.

Tests: two concurrent submit requests (separate DB sessions; and two parallel HTTP requests in
the E2E) → one `SUCCESS`, one 409, one press, one application received.

### 24.8 No automatic retry; ambiguous results

After `submit_triggered_at`, JobPilot never presses again. A press that raised, a browser that
closed or crashed, a page that did not confirm within `SUBMISSION_RESULT_TIMEOUT_SECONDS`
(default 20 s): **`SUBMISSION_UNKNOWN`** — "We could not safely determine whether the
application was submitted. For your safety, JobPilot will NOT submit again automatically."
The application then accepts **no new authorization** (`PREVIOUS_SUBMISSION_UNKNOWN`) until the
user checks with the employer and records it once (`POST …/submissions/{id}/verification
{outcome: SUBMITTED | NOT_SUBMITTED}`); `SUBMITTED` marks the application submitted,
`NOT_SUBMITTED` allows a new review. An attempt left unfinished by a server that stopped is
finished as `SUBMISSION_UNKNOWN` (`INTERRUPTED_AFTER_PRESS`) if `submit_triggered_at` is set,
else as `FAILED_BEFORE_SUBMIT` — never resumed. The database enforces it: `SUCCESS` /
`SUBMISSION_UNKNOWN` require `submit_triggered_at`, `FAILED_BEFORE_SUBMIT` forbids it.

### 24.9 Result verification

A click is not proof. After the press JobPilot reads the page (reads only) until it sees
**evidence** that was *not* there before the press: a recognized confirmation phrase (English /
German: "Thank you for your application", "Your application has been submitted", "Vielen
Dank für Ihre Bewerbung", "Ihre Bewerbung wurde erfolgreich übermittelt" …), a confirmation URL
(`/confirmation`, `/thank-you`, `/success`, `/danke`, `/bestaetigung` …) or an explicit
`data-application-submitted` marker. Then `SUCCESS`, with a safe summary (the recognized phrase
only) and an application reference if one is shown ("Application ID: ABC-12345" — kept only if
it looks like a reference). Nothing else from the page is kept. The browser stays open for the
user (the session is `STOPPED` and the assistant no longer touches it).

### 24.10 Human takeover and invalidation

Human control still wins at every stage. `invalidation.invalidate` (guarded `UPDATE … WHERE
status = 'ACTIVE' / 'AUTHORIZED'`, so it can never overwrite an authorization a submission just
consumed) runs on: take-control, pause, stop, resume, every fill pass, a mapping change, a
document choice or removal, a consent authorization or withdrawal, a navigation / reload / new
window (page marker, checked on every session poll), another website, a security check or login,
and a browser that is gone. Changes made elsewhere — an answer edited in the Questions section,
a field typed in the browser, a re-worded consent — are caught by the hash, which is recomputed
whenever the review is shown, at authorization, and immediately before the press. Nothing is
re-authorized silently: the user reviews and authorizes again. Every review section links back
to editing (answers, CV / Anschreiben, consents) and offers *Take control*.

### 24.11 Audit trail and privacy

`SubmissionAttempt` (`automation_submission_attempts`, append-only once finished — an ORM guard;
only the user's one-time verification may be added) holds: user, application, session,
authorization, final review, reviewed hash, destination site and URL (no query), `started_at`,
`submit_triggered_at`, `finished_at`, result, confirmation reference and summary, failure code
and reason, the user's verification. New events — `FINAL_REVIEW_CREATED`,
`FINAL_REVIEW_INVALIDATED`, `CONSENT_AUTHORIZED`, `CONSENT_REVOKED`, `SUBMISSION_AUTHORIZED`,
`SUBMISSION_AUTHORIZATION_INVALIDATED`, `SUBMISSION_STARTED`, `SUBMISSION_TRIGGERED`,
`SUBMISSION_CONFIRMED`, `SUBMISSION_FAILED`, `SUBMISSION_UNKNOWN`,
`SUBMISSION_VERIFIED_BY_USER` — carry codes and ids only. Never stored or logged: passwords,
cookies, tokens, authorization headers, page content, or submitted values in events and logs
(the reviewed values live only in the user's own `FinalReview` snapshot, as their answers
already do). All V0.10 rules on URLs, private hosts, challenges and logins apply unchanged.

**Application status.** A confirmed `SUCCESS` (or the user's "it was received") sets the
application's existing `submitted` status — the only status change V0.11 makes. Tracking
(interviews, follow-ups, offers, history) is V0.12.

### 24.12 API (V0.11)

```
GET    /applications/{id}/automation/{sid}/final-review              the live state, latest review, authorization
POST   /applications/{id}/automation/{sid}/final-review              {expected_state_hash}          201
POST   /applications/{id}/automation/{sid}/consents/{fid}            {consent_text_hash, give?}     204
DELETE /applications/{id}/automation/{sid}/consents/{fid}                                            204
POST   /applications/{id}/automation/{sid}/submission-authorization  {final_review_id, reviewed_state_hash, confirm: true}  201
GET    /applications/{id}/automation/{sid}/submission-authorization
DELETE /applications/{id}/automation/{sid}/submission-authorization  withdraw an unused authorization 204
POST   /applications/{id}/automation/{sid}/submit                    {authorization_id}             the attempt
GET    /applications/{id}/automation/submissions                     the audit trail
POST   /applications/{id}/automation/submissions/{aid}/verification  {outcome}                      after UNKNOWN
```

`409` codes include `REVIEW_BLOCKED` / `AUTHORIZATION_BLOCKED` (with `details.blockers`),
`REVIEW_CHANGED`, `REVIEW_NOT_CURRENT`, `REVIEW_HASH_MISMATCH`, `STATE_CHANGED`,
`ALREADY_AUTHORIZED`, `AUTHORIZATION_CONSUMED`, `AUTHORIZATION_INVALIDATED`,
`AUTHORIZATION_EXPIRED`, `WRONG_SESSION`, `SUBMISSION_IN_PROGRESS`, `ALREADY_SUBMITTED`,
`PREVIOUS_SUBMISSION_UNKNOWN`, `CONSENT_TEXT_CHANGED`. Another user's anything: `404`. The
submission router is registered before the V0.10 router so `/automation/submissions` is not
read as a session id.

### 24.13 Frontend

Inside the Browser assistant: **Final review** (`assistant/FinalReview.tsx`) — opened by the
user, never automatically — with the six sections, per-consent *I give consent n* / *Withdraw*,
*Edit answers*, *Change CV or Anschreiben*, *Review consents*, *Take control*, *Confirm final
review*, then the authorization box (checkbox + **Authorize & Submit**), *Submitting...*, and
the result (`assistant/SubmissionResult.tsx`): "Application submitted successfully" with
company, position, time and reference; the unknown result with "JobPilot will NOT submit
again automatically" and *Received by the employer* / *Not received*; or "could not be
submitted — no submission was confirmed". `lib/api/submission.ts`, `lib/submission.ts`,
`types/submission.ts`.

### 24.14 Configuration, migration, tests

| Variable | Default | |
| --- | --- | --- |
| `SUBMISSION_AUTHORIZATION_TTL_SECONDS` | `600` | An unused authorization expires. |
| `SUBMISSION_RESULT_TIMEOUT_SECONDS` | `20` | How long to look for a confirmation before `SUBMISSION_UNKNOWN`. |

Migration `c5f2a8e1d9b3` (`v0_11_review_and_submit`), purely additive: tables
`automation_final_reviews`, `automation_consent_authorizations`,
`automation_submission_authorizations`, `automation_submission_attempts`; enum types
`final_review_status`, `submission_authorization_status`, `submission_result`; column
`automation_fields.consent_text`. Composite ownership keys throughout; the exactly-once and
pressed / not-pressed rules as constraints (§24.7–24.8). Verified: upgrade → downgrade →
upgrade with realistic V0.10 data, `alembic check` clean (`tests/test_migrations.py`).

| Area | Where |
| --- | --- |
| hash, final-button rules, confirmation evidence, permits, structure | `tests/test_submission_logic.py` |
| review, consents, authorization, invalidation, submission, exactly once, results (real Chromium) | `tests/test_submission.py` |
| migration roundtrip with V0.10 data | `tests/test_migrations.py` |
| UI | `frontend/__tests__/FinalReview.test.tsx`, `BrowserAssistantSubmission.test.tsx` |
| real Chromium end to end (happy path, no authorization, edit after review, missing consent, concurrent submits, ambiguous result) | `frontend/e2e/v11-review-and-submit.e2e.ts` |

The fake employer site gained `/submit-apply` (really submits; the server confirms with a
reference), `/submit-ambiguous` (never confirms) and `/submit-two-buttons`; every press of a
final button and every received application is counted.

### 24.15 V0.11 limitations

- **Single page, single button.** The final review covers the page in front of the browser.
  Multi-step forms are reviewed and submitted step by step only where the last step holds
  the whole application; pressing "Next" is still never done by JobPilot.
- **Recognition is heuristic.** Final-button captions and confirmation phrases are English and
  German word lists; an unrecognized confirmation is honestly `SUBMISSION_UNKNOWN`, never a
  guessed success. Sites that confirm only by email give `SUBMISSION_UNKNOWN`.
- **Strictness has a cost.** Every field with a mapped question must be answered, typed, or
  left unmapped; custom (ARIA) controls block; two plausible final buttons block. The user can
  always submit by hand.
- **Values outside the page.** The hash proves the page and JobPilot's records match the
  review at the moment of the press; a site's own script changing a value *during* the press
  cannot be ruled out (it would be the site's change, not JobPilot's).
- **One process.** Like V0.10's browsers, the in-flight set used to tell a running attempt from
  an interrupted one is process-local (sticky routing for several workers).
- **Snapshot holds values.** The final review snapshot keeps the reviewed values (the user's own
  data, like their answers) so the review stays exactly what they saw; events and logs never do.

