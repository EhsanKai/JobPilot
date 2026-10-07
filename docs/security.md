# Security (V0.13)

Technical security controls implemented in JobPilot, what the V0.13 review found and
fixed, and what remains. This document describes technical measures; it is not a
certification and does not replace an independent security assessment before a public
launch.

## 1. Threat model in one paragraph

JobPilot holds highly personal data (CVs, application answers, salary expectations,
photos) and can act on the web for its user (the Browser Assistant opens employer sites,
fills forms and — only after explicit authorization — submits). The main risks are: one
user reaching another user's data (IDOR), account takeover (credential stuffing, stolen
tokens), the server being used to reach internal networks (SSRF through job/application
URLs, which come from third parties), malicious uploads, sensitive data leaking through
logs, errors or AI providers, and insecure deployment defaults.

## 2. Authentication and sessions

| Control | Where |
|---|---|
| bcrypt password hashing (per-password salt, cost 12) | `app/core/security.py` |
| Passwords longer than bcrypt's 72-byte limit are **refused** at registration instead of silently truncated (V0.13) | `app/schemas/auth.py` |
| Server-side sessions: every sign-in creates an `auth_sessions` row; the JWT carries only `sub` (user id), `sid` (session id), `iat`, `exp` — no e-mail, name or role | `app/services/auth_service.py`, `app/models/privacy.py` |
| A token is valid only while its session exists, is not revoked and has not expired — checked on **every** request in one query | `auth_service.resolve_session`, `app/api/deps.py` |
| Logout revokes the session on the server (a copied token stops working immediately); "Log out everywhere" revokes all sessions | `POST /auth/logout`, `POST /auth/logout-all` |
| Expired and revoked sessions are removed at the user's next sign-in | `auth_service.start_session` |
| Brute-force protection: 5 failed sign-ins per account and 30 per client address per 15 min → HTTP 429 with `Retry-After`; 10 registrations per address per 15 min | `app/core/rate_limit.py`, settings `LOGIN_*`, `REGISTRATION_MAX_PER_IP` |
| No account enumeration through sign-in: an unknown e-mail costs the same bcrypt time and returns the same 401 body as a wrong password | `security.burn_password_check` |
| Client address for limits: the TCP peer; `X-Forwarded-For` only with `TRUST_PROXY_HEADERS=true` (behind your own proxy) | `app/core/client.py` |
| Tokens are only sent in the `Authorization` header, never in URLs; downloads (photo, screenshot, data export) use `fetch` with the header and `blob:` URLs | `frontend/lib/http.ts` |

**Cookies / CSRF.** JobPilot sets no cookies. The access token lives in `localStorage` and
is sent as a bearer header, so cross-site request forgery does not apply (a forged
cross-site request carries no credentials) and CORS does not allow credentials. The trade-off
is exposure to XSS: mitigated by React's escaping, no `dangerouslySetInnerHTML`, a strict
Content-Security-Policy without third-party script sources, and short-lived, server-revocable
tokens. Moving to an `HttpOnly; Secure; SameSite=Strict` cookie plus CSRF protection is
listed as future work.

## 3. Authorization (IDOR audit)

Every endpoint except `POST /auth/register`, `/auth/login`, `/auth/logout` and the health
checks requires a valid session. `tests/test_security_v013.py` calls **every** API route
without a token and expects 401, so a new unprotected route fails the suite.

Ownership is enforced server-side in the service layer — never by frontend hiding. Lookups
by id always include the user (`... WHERE id = :id AND user_id = :current_user`); a
resource of another user is indistinguishable from a missing one (404). Most child tables
also carry a composite foreign key `(user_id, parent_id)` to their parent, so the database
itself rejects a row that would belong to another user's application or session.

| Resource | Enforcement |
|---|---|
| Profile, professional profile, preferences, skills, languages, deal breakers | no id in the URL for the profile itself; child rows looked up by `id AND user_id` (`profile_service`, `skills_service`, `languages_service`, `preferences_service`) |
| Resumes and resume versions, resume profiles | `resume_service` / `processing_service.get_owned_resume` (`user_id`) |
| Jobs | shared public data; visible only through the user's `user_jobs` row (`job_service.get_visible_job`) |
| User jobs (saved state, discovery) | `user_id` |
| Discovery runs | `run_service.get_owned_run` |
| Matches and explanations | `user_id` on `job_matches` |
| Applications, preparation, context snapshots | `applications/service._owned` |
| Tracking history, status changes, timeline | `tracking._lock_owned` (row lock + `user_id`) |
| Generated documents, CV photo | application ownership + `user_id` on the row; composite FK |
| Application questions, answers, generations | `questions/service._owned_question` (application + `user_id`) |
| Automation sessions, fields, events | `controller.owned_session` / `owned_field` (`user_id` + application) |
| Final reviews, consent authorizations, submission authorizations and attempts | `user_id` + session/application scoping in `automation/submission.py`; composite FKs |
| Privacy data (export, settings, consent records, deletion) | always the signed-in user; no endpoint accepts a user id |

`tests/test_security_v013.py::test_cross_user_access_fails_for_every_owned_resource`
creates one user's resume, job, application, question, photo, skill and desired title and
tries 31 read/write/delete operations on them as a second user: every one is 403/404,
nothing leaks into the error body, and the owner's data is unchanged. Feature suites
(V0.7–V0.12) additionally test isolation of automation sessions, fields, submissions,
documents and tracking.

## 4. Browser Assistant and SSRF

The V0.10/V0.11 Human Control guarantees are unchanged: `SUBMIT_ACTIONS_ALLOWED=False` for
the assistant; CAPTCHA/security checks hand control to the user; consents need the user's
own tick or explicit authorization; submission needs a confirmed final review and a fresh,
explicit, single-use authorization; exactly-once submission; any change after review
invalidates it.

Network egress — **V0.13 fix**:

| Layer | What it checks |
|---|---|
| `safety.validate_application_url` (V0.10) | before opening: http/https only, no credentials in the URL, the host is not loopback/private/link-local/reserved/an intranet name, and does not *resolve* to one |
| page route guard (V0.10) | navigation requests to literal private hosts are aborted |
| **`EgressFilterProxy` (V0.13)** | **every** connection of the browser — navigations, **redirect hops**, images, scripts, fetch/XHR, WebSockets — goes through an in-process forward proxy that resolves the destination itself, refuses it when the name is an intranet name or *any* address is loopback, private, link-local (incl. `169.254.169.254` cloud metadata), multicast, reserved or unspecified, and then connects to the exact address it checked (no DNS-rebinding window). HTTPS is tunnelled, never decrypted. Chromium runs with `--proxy-bypass-list=<-loopback>` (no implicit localhost bypass) and without non-proxied WebRTC UDP. |

The review reproduced two gaps in the V0.10 guard with a real Chromium: a public page that
**redirected** to `http://127.0.0.1/...` was followed (Playwright does not route redirect
hops, and the internal page would have appeared in the user's live screenshot), and a
page's `<img>`/`fetch()` to internal addresses reached them. Both are now blocked;
`tests/test_browser_egress.py` reproduces the attacks and the policy decisions (metadata IP,
IPv6 loopback, private answers for public names, mixed answers, unresolvable names).
`AUTOMATION_ALLOW_PRIVATE_HOSTS=true` (development/tests only) disables the filter and is
refused in production.

Job discovery fetches only fixed, configured API hosts (`boards-api.greenhouse.io`,
`api.lever.co`/`api.eu.lever.co`) with an allow-list check, no redirects, timeouts and a
response-size cap (V0.3/V0.4). No user-supplied URL is fetched server-side.

## 5. Files

| Control | Where |
|---|---|
| Request bodies over `MAX_REQUEST_BODY_MB` (15) are refused with 413 before they are read — by `Content-Length`, and while streaming for chunked bodies | `BodySizeLimitMiddleware` |
| Uploads are read with a bound (`limit + 1` bytes) | `storage/uploads.read_bounded` |
| Resume type is decided from the **content**: PDF magic bytes, or a ZIP that contains `[Content_Types].xml` and `word/document.xml` with entry-count and uncompressed-size caps (zip-bomb guard); the extension must match; the stored MIME type is the verified one, not the browser's claim (V0.13) | `resume_service._verified_type`, `uploads.sniff_document` |
| Photos: JPEG/PNG signatures only, 2 MB | `documents/photos.py` |
| File names are display-only, stripped of directories and control characters and bounded; storage keys are `{user_id}/{uuid}.{ext}` — a name can never choose a path | `uploads.safe_file_name` |
| Local storage resolves every key inside its root (no traversal); per-user deletion accepts only a UUID | `storage/local.py` |
| No storage paths in API responses; files are served only through authenticated, ownership-checked endpoints with `Cache-Control: no-store` and `nosniff` | resumes / photo / export endpoints |
| Generated documents are rendered by JobPilot (python-docx), not from user-supplied templates | `automation/documents.py` |

## 6. HTTP security headers and CORS

API (all responses, including errors and preflights): `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`,
`Cross-Origin-Opener-Policy: same-origin`, `Content-Security-Policy: default-src 'none';
frame-ancestors 'none'; base-uri 'none'` (except the development-only API docs),
`Cache-Control: no-store` on `/api/*`, and `Strict-Transport-Security` when
`SECURITY_HSTS_ENABLED` (default: on in production).

Frontend (`next.config.ts`, all routes): CSP `default-src 'self'`, `connect-src 'self'
<API origin>`, `img-src 'self' blob: data:`, `object-src 'none'`, `frame-ancestors 'none'`,
`form-action 'self'`, `base-uri 'self'`; `nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, COOP; no
`X-Powered-By`. `script-src` keeps `'unsafe-inline'` because the Next.js App Router inlines
its hydration data (a nonce-based CSP would force dynamic rendering of every page) —
documented limitation. HSTS for the frontend belongs on the TLS-terminating proxy.

CORS: explicit origins only (`BACKEND_CORS_ORIGINS`; `"*"` makes startup fail), no
credentials, methods `GET POST PUT PATCH DELETE`, headers `Authorization`, `Content-Type`.

## 7. Logging, errors, secrets

- Log lines use structured event codes (`event=login_failed`, `event=session_revoked`,
  `event=migration_failed`, `event=browser_egress_blocked`, `event=account_deleted`, …) with
  ids, never e-mail addresses, passwords, tokens, CV text, answers or letters. Tests assert
  this for sign-up/sign-in/sign-out and resume processing.
- AI errors are logged by code and exception type only (V0.2 rule).
- Unhandled errors return a generic message; the stack trace goes to the server log only.
- Validation errors no longer echo the submitted value (`input`) — a rejected password or CV
  text is not reflected back (V0.13).
- In production, SQLAlchemy hides bound parameters in errors and logs (`hide_parameters`).
- The production configuration check never prints configured values (it raises a plain
  `InsecureConfigurationError` listing the problems, not a validation error that would echo
  the settings).
- Secrets only come from the environment; none are baked into images; the frontend needs
  none (`NEXT_PUBLIC_*` values are public by definition).

## 8. Configuration safety

`APP_ENV=production` refuses to start with: the development `SECRET_KEY` values or one
shorter than 32 characters, the `jobpilot:jobpilot` database credentials, no CORS origin,
`AUTOMATION_ALLOW_PRIVATE_HOSTS=true`. Any environment refuses `BACKEND_CORS_ORIGINS=*`.
API docs (`/docs`, `/redoc`, `/openapi.json`) are off in production by default.

## 9. Containers

Non-root users (`jobpilot` uid 10001, `node`), read-only application code, only the storage
volume writable, pinned dependencies (`requirements.txt`, `package-lock.json`, version-tagged
base images), health checks, explicit environment, no secrets or `.env` in images, ports
bound to `127.0.0.1` in the development Compose file. See docs/docker-development.md.

## 10. Tests

| Suite | Covers |
|---|---|
| `tests/test_security_v013.py` | unauthenticated access to every route; forged/expired/revoked tokens; logout and logout-everywhere; throttling per account/address and reset on success; enumeration; password length; no echo of submitted values; cross-user matrix; upload content checks, path stripping, size limits (declared and chunked); headers; CORS; production config; error leakage; credentials/tokens/CV text absent from logs; AI pseudonymization and the AI switch |
| `tests/test_browser_egress.py` | SSRF via redirect and subresources with real Chromium; proxy policy (metadata, loopback, IPv6, private DNS answers, mixed answers, intranet names) |
| `tests/test_privacy.py` | export ownership, deletion ownership and completeness, inventory completeness |
| `tests/test_startup.py` | fresh-database startup, migration failure behaviour |
| frontend `__tests__/securityHeaders.test.ts`, `consent.test.tsx`, `PrivacySettings.test.tsx` | CSP and headers; consent behaviour; privacy UI guards |

## 11. Known limitations and next steps

- **Rate limits are per API process** (in memory). One process is what the Compose setup
  runs; several processes need a shared store (e.g. Redis). An attacker can lock a known
  account out for 15 minutes (the usual trade-off of account-based throttling).
- **Token in `localStorage`**: see §2; an `HttpOnly` cookie session is the stronger design.
- **No MFA, no password reset, no e-mail verification yet.**
- **CSP `'unsafe-inline'` for scripts** (Next.js App Router).
- **Malware scanning** of uploads is not implemented (content-type verification only).
- **Uploaded files are not encrypted at rest by JobPilot**; use encrypted volumes / object
  storage with server-side encryption in deployment.
- **Egress filter scope**: it governs the browser's TCP connections. Running the backend
  container in a network without access to internal services (egress firewall) is still
  recommended as defence in depth.
- **No audit log of administrative access** (there is no admin role yet).
- An external penetration test before handling real users' data.
