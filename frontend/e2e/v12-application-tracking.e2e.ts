import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/**
 * V0.12 in a real browser, against the real backend and database: application tracking.
 *
 *     find job -> start application -> prepare -> submit through V0.11 -> APPLIED
 *       -> My Applications -> application tracking page -> timeline
 *     the user changes the status by hand: Follow-up -> Interview -> Offer
 *
 * The submission part runs exactly as in v11-review-and-submit.e2e.ts (a real headless Chromium on
 * the fake employer's form); what is proven here is what tracking does with its result:
 *
 *  - a confirmed submission makes the application Applied -- by the system, from the confirmation;
 *  - an uncertain one does NOT: the user decides, and the timeline says the result is uncertain;
 *  - every manual change is validated by the server, shows up in the timeline, and keeps the
 *    user's note exactly as written;
 *  - My Applications lists, filters and links to the original job; the dashboard counts them;
 *  - another user cannot read any of it.
 *
 * Named to run after v11-review-and-submit.e2e.ts. No e-mail, calendar or AI is involved.
 */

const API = "http://127.0.0.1:8000/api/v1";
const E2E = "http://127.0.0.1:8000/e2e";
const password = "supersecret123";
const RESUME_PDF = path.join(__dirname, "fixtures", "example-resume.pdf");

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

async function siteStats(request: APIRequestContext): Promise<{ submissions: string[]; received: string[] }> {
  return (await request.get(`${E2E}/site`)).json();
}

/** A user, an application on `title`, its data and a tailored CV; the assistant started. */
async function prepared(page: Page, request: APIRequestContext, title: string) {
  const email = `e2e-track-${Date.now()}-${Math.round(Math.random() * 1e6)}@example.com`;
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
  return { email, headers, applicationId, sessionId, base: `${API}/applications/${applicationId}/automation` };
}

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

/** Review, consent, confirm and authorize -- the V0.11 steps the user does themselves. */
async function authorizeAndSubmit(page: Page) {
  const panel = assistant(page);
  await panel.getByRole("button", { name: "Open final review" }).click();
  const review = panel.getByTestId("final-review");
  await expect(review.getByText("1. Destination")).toBeVisible({ timeout: 30_000 });
  await review.getByRole("button", { name: "I give consent 1" }).click();
  await review.getByRole("button", { name: "Confirm final review" }).click();
  await expect(review.getByText(/You confirmed this review/)).toBeVisible();
  await review.getByRole("checkbox", { name: /authorize JobPilot to submit/ }).check();
  await review.getByRole("button", { name: "Authorize & Submit" }).click();
  return panel.getByTestId("submission-result");
}

function row(page: Page, title: string): Locator {
  return page
    .getByRole("list", { name: "Applications" })
    .getByRole("listitem")
    .filter({ hasText: title });
}

async function changeStatus(page: Page, status: string, note?: string) {
  await page.getByLabel("New status").selectOption(status);
  if (note) await page.getByLabel("Note (optional)").fill(note);
  await page.getByRole("button", { name: "Change status" }).click();
  await expect(page.getByText("Status updated.")).toBeVisible();
}

test("a confirmed submission is Applied everywhere; the user then tracks Follow-up, Interview and Offer", async ({
  page,
  request,
}) => {
  const ctx = await prepared(page, request, "Integration Engineer");
  const before = await siteStats(request);

  // Before submitting: tracking says the application is in preparation, with its first history.
  await page.goto("/applications");
  await expect(row(page, "Integration Engineer").locator("[data-status]")).toContainText("In preparation");
  await expect(row(page, "Integration Engineer")).toContainText("Not applied yet");

  // --- Submit through V0.11 ------------------------------------------------------------------------------
  await page.goto(`/applications/${ctx.applicationId}`);
  await fillEverything(page, request, ctx);
  const result = await authorizeAndSubmit(page);
  await expect(result).toContainText("Application submitted successfully.", { timeout: 60_000 });
  const after = await siteStats(request);
  expect(after.submissions.length - before.submissions.length).toBe(1); // tracking never presses anything

  // --- The workspace shows the tracking status and the submission --------------------------------------------
  await page.reload();
  await expect(page.getByLabel("Application status")).toContainText("Applied");
  const card = page.getByRole("heading", { name: "Tracking" }).locator("xpath=ancestor::div[1]");
  await expect(card).toContainText(/Submitted successfully · confirmation EXM-2026-\d{4}/);

  // --- My Applications -----------------------------------------------------------------------------------------
  await page.goto("/applications");
  const mine = row(page, "Integration Engineer");
  await expect(mine.locator("[data-status]")).toContainText("Applied");
  await expect(mine).toContainText("Example GmbH");
  await expect(mine).toContainText(/Submitted successfully · confirmation EXM-2026-\d{4}/);
  await expect(mine).not.toContainText("Not applied yet");
  const original = mine.getByRole("link", { name: /Original job posting/ });
  await expect(original).toHaveAttribute("href", /^https?:\/\//);
  await expect(original).toHaveAttribute("target", "_blank");

  // --- The tracking page: header, summary, timeline -----------------------------------------------------------
  await mine.getByRole("link", { name: "Timeline & status" }).click();
  await expect(page).toHaveURL(new RegExp(`/applications/${ctx.applicationId}/tracking$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Integration Engineer");
  await expect(page.getByLabel("Application status")).toContainText("Applied");
  const timeline = page.getByRole("list", { name: "Application timeline" });
  await expect(timeline.getByRole("listitem")).toHaveCount(4); // saved, preparing, submission, applied
  await expect(timeline).toContainText("Application successfully submitted.");
  await expect(timeline).toContainText(/Result: SUCCESS\. Confirmation: EXM-2026-\d{4}/);
  await expect(timeline).toContainText("Application workspace created.");
  await expect(timeline).toContainText("Job saved.");

  // --- Manual path: Follow-up (with a date), Interview (with a note), Offer ---------------------------------------
  await page.getByLabel("New status").selectOption("FOLLOW_UP");
  await page.getByLabel("Follow up on (optional)").fill("2026-10-21T09:00");
  await page.getByLabel("Note (optional)").fill("Recruiter said they will respond within two weeks.");
  await page.getByRole("button", { name: "Change status" }).click();
  await expect(page.getByText("Status updated.")).toBeVisible();
  await expect(page.getByLabel("Application status")).toContainText("Follow-up");
  await expect(timeline.getByRole("listitem")).toHaveCount(5);
  await expect(timeline).toContainText("Recruiter said they will respond within two weeks.");
  await expect(page.getByRole("heading", { name: "Summary" }).locator("xpath=ancestor::div[1]")).toContainText(
    "21 Oct 2026"
  );

  const note = "Interview invitation received by email.\nBring ID & <b>portfolio</b>.";
  await changeStatus(page, "INTERVIEW", note);
  await expect(page.getByLabel("Application status")).toContainText("Interview");
  await expect(timeline.getByRole("listitem")).toHaveCount(6);
  await expect(timeline.getByRole("listitem").first()).toContainText("You changed the status from Follow-up to Interview.");
  expect(await timeline.getByText("Interview invitation received by email.").first().innerText()).toBe(note);

  await changeStatus(page, "OFFER");
  await expect(page.getByLabel("Application status")).toContainText("Offer");
  await expect(timeline.getByRole("listitem")).toHaveCount(7);
  await expect(timeline.getByRole("listitem").first()).toContainText("Offer");

  // A closed-off transition is refused by the server, and the page says so.
  const refused = await request.patch(`${API}/applications/${ctx.applicationId}/status`, {
    headers: ctx.headers,
    data: { status: "INTERVIEW" },
  });
  expect(refused.status()).toBe(409);
  expect((await refused.json()).error.code).toBe("INVALID_STATUS_TRANSITION");

  // A note, and the follow-up form is gone once the date no longer applies (Offer).
  const noteForm = page.getByRole("form", { name: "Add a note" });
  await noteForm.getByLabel("Note").fill("Waiting for the contract.");
  await noteForm.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByText("Note added.")).toBeVisible();
  await expect(timeline).toContainText("Waiting for the contract.");
  await expect(page.getByRole("form", { name: "Follow-up date" })).toHaveCount(0);

  // --- The history is what the API says, and the submission audit is untouched ----------------------------------------
  const history = (await (await request.get(`${API}/applications/${ctx.applicationId}/timeline`, { headers: ctx.headers })).json())
    .items as { kind: string; status: string | null; source: string | null }[];
  expect(history.filter((e) => e.kind !== "SUBMISSION" && e.kind !== "NOTE").map((e) => e.status).reverse()).toEqual([
    "SAVED",
    "PREPARING",
    "APPLIED",
    "FOLLOW_UP",
    "INTERVIEW",
    "OFFER",
  ]);
  expect(history.find((e) => e.status === "APPLIED")?.source).toBe("SUBMISSION");
  const attempts = await (await request.get(`${ctx.base}/submissions`, { headers: ctx.headers })).json();
  expect(attempts.items).toHaveLength(1);
  expect(attempts.items[0].result).toBe("SUCCESS");
  expect(attempts.items[0].user_verified_outcome).toBeNull();

  // --- My Applications: filters and sorting, and the dashboard count ----------------------------------------------------
  await page.goto("/applications");
  await page.getByLabel("Status").selectOption("OFFER");
  await expect(row(page, "Integration Engineer").locator("[data-status]")).toContainText("Offer");
  await page.getByLabel("Status").selectOption("REJECTED");
  await expect(page.getByText("No applications match your filters.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).first().click();
  await expect(row(page, "Integration Engineer")).toBeVisible();
  await page.getByLabel("Company").fill("example");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(row(page, "Integration Engineer")).toBeVisible();
  await page.getByLabel("Company").fill("no such company");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.getByText("No applications match your filters.")).toBeVisible();
  await page.getByLabel("Sort by").selectOption("applied");
  await page.getByRole("button", { name: "Clear filters" }).first().click();
  await expect(row(page, "Integration Engineer")).toBeVisible();

  await page.goto("/dashboard");
  const summary = page.getByRole("heading", { name: "Applications" }).locator("xpath=ancestor::div[2]");
  await expect(summary.getByText("Offer")).toBeVisible();
  await summary.getByRole("link", { name: "My Applications" }).click();
  await expect(page).toHaveURL(/\/applications$/);

  // --- Nobody else can see or change any of it ---------------------------------------------------------------------------
  const otherEmail = `e2e-track-other-${Date.now()}@example.com`;
  expect((await request.post(`${API}/auth/register`, {
    data: { email: otherEmail, password, first_name: "Max", last_name: "Muster" },
  })).status()).toBe(201);
  const other = { Authorization: `Bearer ${await apiToken(request, otherEmail)}` };
  expect((await request.get(`${API}/applications/${ctx.applicationId}/timeline`, { headers: other })).status()).toBe(404);
  expect((await request.get(`${API}/applications/${ctx.applicationId}`, { headers: other })).status()).toBe(404);
  expect((await request.patch(`${API}/applications/${ctx.applicationId}/status`, {
    headers: other,
    data: { status: "WITHDRAWN" },
  })).status()).toBe(404);
  expect((await request.patch(`${API}/applications/${ctx.applicationId}/tracking`, {
    headers: other,
    data: { note: "hi" },
  })).status()).toBe(404);
  const theirs = await (await request.get(`${API}/applications`, { headers: other })).json();
  expect(theirs.total).toBe(0);
});

test("an uncertain submission is not Applied: the user decides, and the timeline keeps the uncertainty", async ({
  page,
  request,
}) => {
  const ctx = await prepared(page, request, "QA Engineer");
  await fillEverything(page, request, ctx);
  const result = await authorizeAndSubmit(page);
  await expect(result).toContainText("We could not safely determine whether the application was submitted.", {
    timeout: 60_000,
  });

  // Tracking: still in preparation, with a clear call to check.
  await page.goto("/applications");
  const mine = row(page, "QA Engineer");
  await expect(mine.locator("[data-status]")).toContainText("In preparation");
  await expect(mine).toContainText("Submission result uncertain — user verification required");
  await expect(mine).not.toContainText("Submitted successfully");
  await expect(mine).toContainText("Not applied yet");
  const summary = await (await request.get(`${API}/applications/summary`, { headers: ctx.headers })).json();
  expect(summary.counts.APPLIED).toBe(0);
  expect(summary.counts.PREPARING).toBe(1);

  await mine.getByRole("link", { name: "Timeline & status" }).click();
  const timeline = page.getByRole("list", { name: "Application timeline" });
  await expect(timeline).toContainText("Submission result uncertain — user verification required.");
  await expect(page.getByRole("status")).toContainText("couldn't tell whether the submission went through");

  // The user checked with the employer and records it themselves.
  await changeStatus(page, "APPLIED", "Confirmed by email from the recruiter.");
  await expect(page.getByLabel("Application status")).toContainText("Applied");
  await expect(timeline.getByRole("listitem").first()).toContainText("You changed the status from In preparation to Applied.");
  await expect(timeline).toContainText("Confirmed by email from the recruiter.");
  // ... and the uncertain attempt is still on the record, unchanged.
  await expect(timeline).toContainText("Submission result uncertain");
  const attempts = await (await request.get(`${ctx.base}/submissions`, { headers: ctx.headers })).json();
  expect(attempts.items[0].result).toBe("SUBMISSION_UNKNOWN");
  expect(attempts.items[0].user_verified_outcome).toBeNull();
});
