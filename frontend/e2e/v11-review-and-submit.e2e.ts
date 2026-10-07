import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/**
 * V0.11 in a real browser, against the real backend and database: review & submit.
 *
 *     AI prepares the application; you review it and authorize it; only then does JobPilot
 *     press the employer's final button -- once.
 *
 * The API drives a real headless Chromium against the fake employer website
 * (backend/tests/fake_site.py): `/submit-apply` really submits (the fake server counts the
 * application and shows a confirmation page), `/submit-ambiguous` never confirms anything.
 * `/e2e/site` counts every press of a final button (`submissions`) and every application
 * the employer received (`received`). `/e2e/human/...` stands in for the person at the window.
 *
 * Proven: the happy path ends in a confirmed SUCCESS after exactly one press; with no
 * authorization, after an edit, or without the consent nothing is pressed; an ambiguous
 * page is SUBMISSION_UNKNOWN after one press and is never pressed again; two concurrent
 * submit requests press once. Named to run after v10-browser-assistant.e2e.ts.
 */

const API = "http://127.0.0.1:8000/api/v1";
const E2E = "http://127.0.0.1:8000/e2e";
const password = "supersecret123";
const RESUME_PDF = path.join(__dirname, "fixtures", "example-resume.pdf");
const PRIVACY =
  "I agree to the processing of my personal data for this application in accordance with the privacy policy";

type Headers = { Authorization: string };

async function register(page: Page, address: string) {
  await page.goto("/register");
  await page.getByLabel("First name").fill("Erika");
  await page.getByLabel("Last name").fill("Mustermann");
  await page.getByLabel("Email").fill(address);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function apiToken(request: APIRequestContext, address: string): Promise<string> {
  const response = await request.post(`${API}/auth/login`, { data: { email: address, password } });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}

async function confirmResume(request: APIRequestContext, headers: Headers) {
  const upload = await request.post(`${API}/resumes`, {
    headers,
    multipart: {
      file: { name: "alex-example.pdf", mimeType: "application/pdf", buffer: readFileSync(RESUME_PDF) },
    },
  });
  expect(upload.status()).toBe(201);
  const resume = (await upload.json()) as { id: string };
  expect((await request.post(`${API}/resumes/${resume.id}/process`, { headers, data: {} })).status()).toBe(202);
  await expect
    .poll(async () => {
      const profile = await (await request.get(`${API}/resumes/${resume.id}/profile`, { headers })).json();
      return profile.profile?.status ?? profile.latest_attempt?.status;
    })
    .toBe("needs_review");
  expect((await request.post(`${API}/resumes/${resume.id}/profile/accept-pending`, { headers })).ok()).toBeTruthy();
  expect((await request.post(`${API}/resumes/${resume.id}/profile/confirm`, { headers })).ok()).toBeTruthy();
}

async function startApplication(page: Page, title: string): Promise<string> {
  expect((await page.request.post(`${E2E}/submit-vacancies`)).ok()).toBeTruthy();
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Discover jobs" }).click();
  await expect(page.getByText(/Discovery finished/)).toBeVisible({ timeout: 60_000 });
  await page.getByRole("article", { name: title }).getByRole("link", { name: "Details" }).click();
  await page.getByRole("button", { name: "Start Application" }).click();
  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
  return page.url().split("/").pop()!;
}

function assistant(page: Page): Locator {
  return page.locator("#browser-assistant");
}

async function human(request: APIRequestContext, sessionId: string, action: string, body: object) {
  const response = await request.post(`${E2E}/human/${sessionId}/${action}`, { data: body });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function siteStats(request: APIRequestContext): Promise<{ submissions: string[]; received: string[] }> {
  return (await request.get(`${E2E}/site`)).json();
}

/** A user, an application on `title`, its data and a tailored CV; the assistant started. */
async function prepared(page: Page, request: APIRequestContext, title: string) {
  const email = `e2e-submit-${Date.now()}-${Math.round(Math.random() * 1e6)}@example.com`;
  await register(page, email);
  const headers = { Authorization: `Bearer ${await apiToken(request, email)}` };
  await confirmResume(request, headers);
  const applicationId = await startApplication(page, title);
  const patched = await request.patch(`${API}/applications/${applicationId}`, {
    headers,
    data: {
      application_language: "en",
      salary_expectation: "78000",
      salary_currency: "EUR",
      mention_salary_in_letter: false,
      available_from: "2027-01-01",
      work_authorization: "authorized",
    },
  });
  expect(patched.ok()).toBeTruthy();
  expect((await request.post(`${API}/applications/${applicationId}/documents/cv`, { headers })).status()).toBe(201);
  await page.reload();

  const panel = assistant(page);
  await panel.getByRole("button", { name: "Start browser assistant" }).click();
  await expect(panel.getByTestId("assistant-status")).toContainText(/Needs your|Running/, { timeout: 60_000 });
  const sessions = await (await request.get(`${API}/applications/${applicationId}/automation`, { headers })).json();
  const sessionId = sessions.items[0].id as string;
  const base = `${API}/applications/${applicationId}/automation`;
  return { headers, applicationId, sessionId, base, panel };
}

/** The user answers what application data does not answer (in the Questions section; via the API here). */
async function answerQuestions(request: APIRequestContext, ctx: Awaited<ReturnType<typeof prepared>>) {
  const answers: Record<string, string> = {
    "Email address": "erika@example.com",
    "Phone number": "+49 170 1234567",
    "Years of experience": "6",
    "Why do you want to join us?": "I enjoy building reliable integrations.",
    "Are you legally authorized to work in Germany?": "Yes",
  };
  const fields = (await (await request.get(`${ctx.base}/${ctx.sessionId}/fields`, { headers: ctx.headers })).json())
    .items as { label: string; question: { id: string } | null; answer: { state: string } | null }[];
  for (const field of fields) {
    const text = answers[field.label];
    if (!text || !field.question || field.answer?.state === "APPROVED") continue;
    const response = await request.post(
      `${API}/applications/${ctx.applicationId}/questions/${field.question.id}/manual`,
      { headers: ctx.headers, data: { answer: text } }
    );
    expect(response.ok(), await response.text()).toBeTruthy();
  }
}

/** Answers, the CV ("Use this file"), a fill -- everything but the consent and the review. */
async function fillEverything(page: Page, request: APIRequestContext, ctx: Awaited<ReturnType<typeof prepared>>) {
  await answerQuestions(request, ctx);
  await page.reload();
  const panel = assistant(page);
  const cv = panel.getByRole("row", { name: "Field Resume / CV" });
  await cv.getByRole("button", { name: "Use this file for Resume / CV" }).click();
  await expect(cv.getByText("Chosen:")).toBeVisible();
  await panel.getByRole("button", { name: "Fill approved answers" }).click();
  await expect(cv.getByText("Filled", { exact: true })).toBeVisible({ timeout: 30_000 });
}

async function openFinalReview(page: Page): Promise<Locator> {
  const panel = assistant(page);
  await panel.getByRole("button", { name: "Open final review" }).click();
  const review = panel.getByTestId("final-review");
  await expect(review.getByText("1. Destination")).toBeVisible({ timeout: 30_000 });
  return review;
}

test("review, authorize and submit an application -- exactly once, with a confirmation", async ({ page, request }) => {
  const ctx = await prepared(page, request, "Integration Engineer");
  const before = await siteStats(request);
  await fillEverything(page, request, ctx);

  // --- Final review: what, where, from which source ---------------------------------------------
  const review = await openFinalReview(page);
  await expect(review.getByTestId("destination-host")).toHaveText("127.0.0.1");
  await expect(review.getByText("Example GmbH")).toBeVisible();
  const personal = review.getByRole("table", { name: "Personal information" });
  await expect(personal.getByRole("row", { name: "Review First name" })).toContainText("Erika");
  await expect(personal.getByRole("row", { name: "Review First name" })).toContainText("From your application data");
  const questions = review.getByRole("table", { name: "Application questions" });
  await expect(questions.getByRole("row", { name: "Review Why do you want to join us?" })).toContainText(
    "I enjoy building reliable integrations."
  );
  await expect(questions.getByRole("row", { name: "Review Why do you want to join us?" })).toContainText("Your answer");
  await expect(review.getByRole("list", { name: "Documents" })).toContainText("Tailored CV v1");

  // --- Consents: each one, explicitly -----------------------------------------------------------------
  const consents = review.getByRole("list", { name: "Consents" });
  await expect(consents.getByRole("listitem", { name: "Consent 1" })).toContainText(PRIVACY);
  await expect(review.getByRole("list", { name: "Blockers" })).toContainText("needs your explicit authorization");
  await expect(review.getByRole("button", { name: "Confirm final review" })).toBeDisabled();
  await review.getByRole("button", { name: "I give consent 1" }).click();
  await expect(consents.getByRole("listitem", { name: "Consent 1" })).toContainText("Authorized by you");
  await expect(consents.getByRole("listitem", { name: "Consent 2" })).toContainText("Not authorized by you");
  expect((await human(request, ctx.sessionId, "checked", { selector: "#privacy" })).checked).toBe(true);
  expect((await human(request, ctx.sessionId, "checked", { selector: "#talent_pool" })).checked).toBe(false);
  await expect(review.getByText("None.")).toBeVisible();

  // --- Confirm the review, then the explicit authorization --------------------------------------------
  await review.getByRole("button", { name: "Confirm final review" }).click();
  await expect(review.getByText(/You confirmed this review/)).toBeVisible();
  const submit = review.getByRole("button", { name: "Authorize & Submit" });
  await expect(submit).toBeDisabled();
  expect((await siteStats(request)).submissions.length).toBe(before.submissions.length); // nothing pressed yet
  await review.getByRole("checkbox", { name: /I have reviewed the information above and authorize JobPilot/ }).check();
  await submit.click();

  // --- The result: confirmed by the employer's page ------------------------------------------------------
  const result = assistant(page).getByTestId("submission-result");
  await expect(result).toContainText("Application submitted successfully.", { timeout: 60_000 });
  await expect(result).toContainText("Example GmbH");
  await expect(result).toContainText(/Application reference EXM-2026-\d{4}/);

  const after = await siteStats(request);
  expect(after.submissions.length - before.submissions.length).toBe(1); // pressed exactly once
  expect(after.received.length - before.received.length).toBe(1); // received exactly once

  const attempts = await (await request.get(`${ctx.base}/submissions`, { headers: ctx.headers })).json();
  expect(attempts.submitted).toBe(true);
  expect(attempts.items).toHaveLength(1);
  expect(attempts.items[0].result).toBe("SUCCESS");
  const text = JSON.stringify(attempts);
  expect(text).not.toMatch(/cookie|password|integrations\./i);
  await page.reload();
  // V0.12: a confirmed submission is the tracking status "Applied" (V0.11 showed "Submitted").
  await expect(page.getByLabel("Application status")).toContainText("Applied");
});

test("nothing is pressed without an authorization, after an edit, or without the consent; concurrent submits press once", async ({
  page,
  request,
}) => {
  const ctx = await prepared(page, request, "Integration Engineer");
  const before = await siteStats(request);
  await fillEverything(page, request, ctx);

  // Case 3: the consent was not explicitly authorized -> blocked.
  const review = await openFinalReview(page);
  await expect(review.getByRole("list", { name: "Blockers" })).toContainText("needs your explicit authorization");
  await expect(review.getByRole("button", { name: "Confirm final review" })).toBeDisabled();
  const blocked = await (await request.get(`${ctx.base}/${ctx.sessionId}/final-review`, { headers: ctx.headers })).json();
  const refused = await request.post(`${ctx.base}/${ctx.sessionId}/final-review`, {
    headers: ctx.headers,
    data: { expected_state_hash: blocked.current.state_hash },
  });
  expect(refused.status()).toBe(409);

  await review.getByRole("button", { name: "I give consent 1" }).click();
  await review.getByRole("button", { name: "Confirm final review" }).click();
  await expect(review.getByText(/You confirmed this review/)).toBeVisible();

  // Case 1: no authorization -> no submission.
  const noAuth = await request.post(`${ctx.base}/${ctx.sessionId}/submit`, {
    headers: ctx.headers,
    data: { authorization_id: "00000000-0000-4000-8000-000000000000" },
  });
  expect(noAuth.status()).toBe(404);
  // ... and the request cannot name a URL or a button either.
  const tampered = await request.post(`${ctx.base}/${ctx.sessionId}/submit`, {
    headers: ctx.headers,
    data: { authorization_id: "00000000-0000-4000-8000-000000000000", url: "https://evil.example.org" },
  });
  expect(tampered.status()).toBe(422);

  // Case 2: the user edits a field after the final review -> the review is no longer current.
  const reviewed = await (await request.get(`${ctx.base}/${ctx.sessionId}/final-review`, { headers: ctx.headers })).json();
  expect(reviewed.review_is_current).toBe(true);
  await human(request, ctx.sessionId, "type", { selector: "#phone", text: "+49 30 0000000" });
  await review.getByRole("button", { name: "Check again" }).click();
  await expect(review.getByText(/no longer current/)).toBeVisible();
  await expect(review.getByRole("button", { name: "Authorize & Submit" })).toHaveCount(0);
  const stale = await request.post(`${ctx.base}/${ctx.sessionId}/submission-authorization`, {
    headers: ctx.headers,
    data: { final_review_id: reviewed.review.id, reviewed_state_hash: reviewed.review.state_hash, confirm: true },
  });
  expect(stale.status()).toBe(409);
  expect((await siteStats(request)).submissions.length).toBe(before.submissions.length);

  // Case 5: reviewed and authorized again -- two submit requests at the same time press once.
  await review.getByRole("button", { name: "Confirm final review" }).click();
  await expect(review.getByText(/You confirmed this review/)).toBeVisible();
  const current = await (await request.get(`${ctx.base}/${ctx.sessionId}/final-review`, { headers: ctx.headers })).json();
  const authorization = await (
    await request.post(`${ctx.base}/${ctx.sessionId}/submission-authorization`, {
      headers: ctx.headers,
      data: { final_review_id: current.review.id, reviewed_state_hash: current.review.state_hash, confirm: true },
    })
  ).json();
  expect(authorization.status).toBe("AUTHORIZED");
  const responses = await Promise.all(
    [0, 1].map(() =>
      request.post(`${ctx.base}/${ctx.sessionId}/submit`, {
        headers: ctx.headers,
        data: { authorization_id: authorization.id },
      })
    )
  );
  const statuses = responses.map((r) => r.status()).sort();
  expect(statuses).toEqual([200, 409]);
  const winner = (await responses.find((r) => r.status() === 200)!.json()) as { result: string };
  expect(winner.result).toBe("SUCCESS");
  const after = await siteStats(request);
  expect(after.submissions.length - before.submissions.length).toBe(1);
  expect(after.received.length - before.received.length).toBe(1);
});

test("an ambiguous result is SUBMISSION_UNKNOWN after one press, and is never pressed again", async ({ page, request }) => {
  const ctx = await prepared(page, request, "QA Engineer");
  const before = await siteStats(request);
  await fillEverything(page, request, ctx);
  const review = await openFinalReview(page);
  await review.getByRole("button", { name: "I give consent 1" }).click();
  await review.getByRole("button", { name: "Confirm final review" }).click();
  await expect(review.getByText(/You confirmed this review/)).toBeVisible();
  await review.getByRole("checkbox", { name: /authorize JobPilot to submit/ }).check();
  await review.getByRole("button", { name: "Authorize & Submit" }).click();

  const result = assistant(page).getByTestId("submission-result");
  await expect(result).toContainText("We could not safely determine whether the application was submitted.", {
    timeout: 60_000,
  });
  await expect(result).toContainText("JobPilot will NOT submit again automatically");
  await expect(result).not.toContainText("submitted successfully");
  await expect(assistant(page).getByRole("button", { name: /retry|try again/i })).toHaveCount(0);

  const attempts = await (await request.get(`${ctx.base}/submissions`, { headers: ctx.headers })).json();
  expect(attempts.items[0].result).toBe("SUBMISSION_UNKNOWN");
  expect(attempts.needs_verification).toBe(true);
  // The same authorization cannot be used again.
  const again = await request.post(`${ctx.base}/${ctx.sessionId}/submit`, {
    headers: ctx.headers,
    data: { authorization_id: attempts.items[0].authorization_id },
  });
  expect(again.status()).toBe(409);
  await page.waitForTimeout(1500);
  const after = await siteStats(request);
  expect(after.submissions.length - before.submissions.length).toBe(1); // one press, no retry
  expect(after.received.length - before.received.length).toBe(0); // the employer confirmed nothing

  // The user checks and records what they found.
  await result.getByRole("button", { name: "Not received" }).click();
  await expect(assistant(page).getByText(/You recorded: the application was not received/)).toBeVisible();
});
