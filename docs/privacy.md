# Privacy & data processing (V0.13)

> **Status.** JobPilot implements technical privacy and security controls that support data
> protection compliance (GDPR / DSGVO, and the German TDDDG for terminal-equipment storage).
> **Technical privacy and security controls are implemented; legal compliance requires a
> final review** by a qualified lawyer or data protection officer for the concrete
> deployment (who operates it, for whom, where, with which contracts). Nothing in this
> repository makes or claims legal compliance on its own. Section 9 lists the decisions that
> are legal, not technical.

## 1. Architecture at a glance

```
Browser ── localStorage: sign-in token only (no cookies, no analytics, no third-party scripts)
   │ HTTPS (deployment), bearer token
   ▼
Backend API ──► PostgreSQL         all structured data (profile, resumes' extracted facts,
   │                                jobs list, applications, documents, answers, tracking,
   │                                Browser Assistant audit trail, consent records, sessions)
   ├──► File storage                uploaded resume files and CV photos, under {user_id}/
   ├──► AI provider (Anthropic)     only when the user uses an AI feature, only the data that
   │                                feature needs, and never when the user switched it off
   ├──► Job boards (Greenhouse, Lever public APIs)   reads public job ads; sends no user data
   └──► Employer websites (Browser Assistant)       what the user approved, when the user acts
```

## 2. What JobPilot stores, why, where, for how long, who can access it

The authoritative, machine-checked inventory is `backend/app/services/privacy/inventory.py`:
every database table is either user-owned (exported and deleted with the account) or
shared, and `tests/test_privacy.py` fails if a new table is not classified. The same
inventory drives "My data", the export and the deletion report.

| Category | Data | Purpose | Where | Retention | Access |
|---|---|---|---|---|---|
| Account | e-mail, name, password hash, AI-processing choice | sign-in, identification | PostgreSQL | until account deletion | the user; operators with database access |
| Profile | phone, location, LinkedIn URL, job title, experience, seniority, industries, skills, languages | matching, documents | PostgreSQL | until the user deletes it / the account (**no period decided**) | same |
| Job preferences | titles, locations, work models, salary range, deal breakers | discovery, ranking | PostgreSQL | same | same |
| Resumes | original files; extracted facts with evidence; the user's review; candidate profile snapshots | source of truth for matching and documents | files + PostgreSQL | same | same; AI provider (see §4) when analyzing |
| Jobs & matches | discovery runs, the user's job list, match results and explanations | job search | PostgreSQL | same | same |
| Applications | preparation data (salary expectation, availability, notice period, work authorization, notes), questions and answers, generated CVs/letters, CV photo, status history | applying, tracking | PostgreSQL + files (photo) | same | same; AI provider for generation; the employer when the user submits |
| Browser Assistant | page fields seen, values filled, events, final reviews, consents given on employer forms, submission authorizations and results | Human Control audit trail | PostgreSQL | same | same |
| Privacy & sign-ins | consent/choice records; sign-in sessions (start, expiry, revocation — **no IP address, no device data**) | proving the user's choices; session security | PostgreSQL | choices: until account deletion; expired/ended sessions: removed at the next sign-in | same |
| Logs | event codes with user/record ids, no content | operation, security | container stdout (operator's log system) | operator's decision (**not defined in code**) | operators |

Shared, not personal: job advertisements as published by employers, JobPilot's analysis of
them, and name catalogs (skills, languages, industries).

Nothing is deleted automatically yet. Retention periods for inactive accounts, old resume
versions, finished applications, Browser Assistant records and logs are **open product /
legal decisions** (§9) — they are deliberately not invented here.

## 3. Rights support (technical)

| Right (GDPR) | Technical support in JobPilot |
|---|---|
| Access (Art. 15) and portability (Art. 20) | Settings → Privacy & data → **Download my data**: a ZIP with all user-owned records as JSON (grouped and explained by category) plus the original uploaded files. Password hashes and internal storage paths are excluded. |
| Rectification (Art. 16) | Profile, preferences, resume review, application data and answers are editable by the user. |
| Erasure (Art. 17) | **Delete my account** (password + typed phrase `DELETE MY ACCOUNT`): closes the user's live browser sessions, deletes every user-owned row in one transaction (ON DELETE CASCADE from `users`, verified by tests), then removes the user's file folder; reports what was deleted. Shared job data is kept (it is not the user's). Applications already sent to employers are outside JobPilot's control. |
| Objection / restriction regarding AI processing (Art. 18, 21) | **External AI processing** switch: when off, every AI feature refuses server-side before anything is sent; deterministic features keep working. |
| Information (Art. 13) | "My data" and "Privacy information" in Settings show categories, purposes, retention status, recipients, browser storage. |

Organizational parts — identity verification for requests outside the app, response
deadlines (Art. 12(3)), the privacy notice text, records of processing (Art. 30) — are not
software features and need the operator's process.

## 4. AI provider (Anthropic) — data audit

JobPilot calls the AI provider only through `app/ai/providers/anthropic.py`, only for the
features below, only for a user who has not switched AI processing off, and sends no user
id, e-mail or other metadata with the request.

| Feature | Sent | Not sent |
|---|---|---|
| Resume analysis | resume text with **e-mail addresses and phone numbers replaced by placeholders** (`[EMAIL_1]`, `[PHONE_1]`; restored locally in the result) | real e-mail/phone (international or labelled numbers); the file itself |
| Tailored CV / cover letter | facts from the user's confirmed resume, the job ad, application facts (availability, notice period, stated work authorization), match strengths/gaps | contact header (name, e-mail, phone, address — added locally), salary, photo |
| Humanize (letter rewording) | the letter's paragraphs and the facts they cite, job title, company | contact header, salary |
| Application question answers | the question, the job ad, resume facts, application facts | salary; contact details (deterministic answers fill e-mail/phone/LinkedIn locally without AI) |
| Form field mapping (Browser Assistant) | the field's label/type/options and the user's question texts | answers, page content, values |
| Match explanation | the deterministic match result (scores, strengths, gaps, unknowns) and job title/company | profile, resume |

What Anthropic's official documentation states (checked 2026-10-07, platform.claude.com):

- API data is not used for model training without the customer's express permission.
- Zero data retention (ZDR) is available by agreement with Anthropic; without it the
  standard commercial retention policy applies (privacy.claude.com, "How long do you store
  my organization's data?" — **verify the current period before relying on it**).
- Inference location: the API offers `inference_geo` `"global"` (default) or `"us"`; data
  at rest (workspace geo) is `"us"` only. **There is currently no EU-only processing
  option**, so using the Anthropic API means a transfer to a third country.

JobPilot does not set `inference_geo` (US-only would not avoid the transfer). No contractual
status (DPA, transfer mechanism) is implied by this code. Technical levers available today:
`AI_PROVIDER=disabled` (no AI at all), the per-user switch, `AI_PROCESSING_DEFAULT_ALLOWED=false`
(opt-in for new accounts), and contact pseudonymization (`AI_PSEUDONYMIZE_CONTACT_DETAILS`).

Limits of minimization: a resume necessarily contains personal data (name, employers,
education, sometimes address or date of birth, and possibly special categories such as
health information a candidate chose to include). Only e-mail addresses and phone numbers
are pseudonymized; names and street addresses in the resume text are sent as they are.

## 5. Cookies, local storage and consent

**Current use** (determined from the code, V0.13): JobPilot sets **no cookies**, loads **no
analytics, tracking, marketing pixels, third-party scripts, fonts or embedded services**
(the frontend CSP allows no third-party origin). The only browser storage is the sign-in
token in `localStorage` (`jobpilot_access_token`), removed at logout.

Assessment input for the legal review: § 25 TDDDG requires consent for storing or reading
information on the user's device unless it is *unbedingt erforderlich* (strictly necessary)
for a digital service the user explicitly requested (§ 25 (2) Nr. 2). Keeping a signed-in
user signed in is presented here as strictly necessary; confirming that is a legal call.

Because there is nothing optional, JobPilot shows **no cookie banner** — adding tracking
just to show one would be the wrong way round. The architecture for when that changes
(`frontend/lib/consent.ts`, `components/privacy/ConsentBanner.tsx`, `CookiePreferences.tsx`):

- optional categories are registered in one place; the banner appears automatically once
  one exists, and nothing optional may run unless `hasConsent(category)` is true;
- "Accept all" and "Reject all" are equal choices on the same layer (EDPB Cookie Banner
  Taskforce report, 2023), with per-category choices one click away;
- the decision (category, policy version, timestamp — no personal data) is stored in the
  browser; a new version asks again; Settings → Cookie preferences changes or **withdraws**
  it at any time, as easily as it was given (Art. 7(3) GDPR; EDPB Guidelines 05/2020).

Server-side consent records (`consent_records`: category, policy version, decision, time)
exist today for the external-AI-processing choice. Whether that choice must be an opt-in
*consent* or can rest on another legal basis is a legal decision (§9); technically it is
recorded, versioned and revocable either way.

## 6. Security measures relevant to privacy (Art. 32)

See docs/security.md: server-side sessions with revocation, brute-force throttling,
ownership checks on every resource, upload validation, SSRF egress filter for the browser,
security headers, no secrets/content in logs, non-root containers, production configuration
checks.

## 7. Data minimization decisions in V0.13

- Sign-in sessions store no IP address or device data.
- Rate limiting holds e-mail addresses only as SHA-256 hashes, in memory.
- Consent records hold no free text.
- The data export omits password hashes and internal storage paths.
- AI payloads exclude contact details where the task does not need them (§4).

## 8. Deletion details

What is deleted: every table listed in the inventory's user-owned categories (profile,
preferences, resumes and extracted facts, job list, matches, applications, documents,
photos, questions and answers, tracking history, Browser Assistant records, consent
records, sessions) and the user's file folder. Live Browser Assistant windows are closed
first. What stays: shared job advertisements and catalogs; server logs (ids only) per the
operator's log retention; backups per the operator's backup policy (JobPilot ships no
backup system — **operators must define how deletions propagate to backups**). No legal
retention obligation is implemented; none is assumed.

## 9. Decisions that require legal / data-protection review

1. Controller/processor roles for the concrete deployment; privacy notice (Art. 13/14) and
   imprint; records of processing (Art. 30).
2. Legal basis per processing purpose (contract, consent, legitimate interest), in
   particular for sending CV data to an AI provider — and therefore whether the AI switch
   must default to off (`AI_PROCESSING_DEFAULT_ALLOWED=false`).
3. Special categories of data (Art. 9) that candidates may include in CVs or answers
   (health, religion, union membership …); photos.
4. AI provider: data processing agreement with Anthropic; international transfer
   mechanism (EU–US Data Privacy Framework participation status or SCCs, transfer impact
   assessment); whether ZDR is required; DPIA (Art. 35) for AI-assisted processing of
   applicant data.
5. Automated decision-making (Art. 22): JobPilot ranks jobs for the candidate and never
   decides about a person; confirm that assessment.
6. Retention periods: inactive accounts, resume versions, applications, Browser Assistant
   audit trail, consent records after withdrawal, logs, backups.
7. § 25 TDDDG assessment of the sign-in token in localStorage as strictly necessary.
8. Browser Assistant: terms of use of employer sites; submitting on the user's behalf;
   handling of employer consent checkboxes (already user-authorized only).
9. Job board data: terms of the Greenhouse / Lever public APIs; storing employer postings.
10. Breach notification process (Art. 33/34), DPO requirement (Art. 37), security incident
    contact.

## 10. Sources

- Regulation (EU) 2016/679 (GDPR), EUR-Lex: https://eur-lex.europa.eu/eli/reg/2016/679/oj
- EDPB Guidelines 05/2020 on consent (v1.1):
  https://www.edpb.europa.eu/system/files/documents/files/file1/edpb_guidelines_202005_consent_en.pdf
- EDPB Report of the Cookie Banner Taskforce (17 January 2023):
  https://www.edpb.europa.eu/system/files/2023-01/edpb_20230118_report_cookie_banner_taskforce_en.pdf
- EDPB Guidelines 4/2019 on Article 25 (data protection by design and by default):
  https://www.edpb.europa.eu/system/files/documents/files/file1/edpb_guidelines_201904_dataprotection_by_design_and_by_default_v2.0_en.pdf
- § 25 TDDDG: https://www.gesetze-im-internet.de/ttdsg/__25.html
- DSK Orientierungshilfe für Anbieter von digitalen Diensten:
  https://www.datenschutzkonferenz-online.de/media/oh/OH_Digitale_Dienste.pdf
- Anthropic, API and data retention: https://platform.claude.com/docs/en/manage-claude/api-and-data-retention
- Anthropic, Data residency: https://platform.claude.com/docs/en/manage-claude/data-residency
- Anthropic, commercial data retention policy:
  https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data
