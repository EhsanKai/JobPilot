import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/**
 * V0.10 in a real browser, against the real backend and database: the browser assistant.
 *
 *     JobPilot fills forms; the user remains in control.
 *
 * The API drives a real headless Chromium against a local fake employer website
 * (backend/tests/fake_site.py). `/e2e/human/...` is a test-only stand-in for the person at
 * that browser window: it types into the very page the assistant works in, outside the
 * assistant, the way a user does. The AI is the deterministic stub of backend/e2e/serve.py.
 *
 * What it proves: the assistant starts only when asked, discovers the form, maps its fields
 * to questions, fills the approved answers and verifies them in the page; the user takes
 * control and the assistant changes nothing; resuming keeps the user's edits and fills only
 * what is empty; legal confirmations are never ticked; a file is attached only after "Use this
 * file"; a CAPTCHA stops the assistant and hands the browser to the user -- and the final
 * "Submit application" button on the page was never clicked. Named to run after
 * v09-application-questions.e2e.ts.
 */

const API = "http://127.0.0.1:8000/api/v1";
const E2E = "http://127.0.0.1:8000/e2e";
const password = "supersecret123";
const RESUME_PDF = path.join(__dirname, "fixtures", "example-resume.pdf");

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

async function confirmResume(request: APIRequestContext, token: string) {
  const headers = { Authorization: `Bearer ${token}` };
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

/** Discover jobs, open one by title and start its application. Returns the application id. */
async function startApplication(page: Page, title: string): Promise<string> {
  // The two vacancies whose application page is the fake employer website.
  expect((await page.request.post(`${E2E}/browser-vacancies`)).ok()).toBeTruthy();
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

function fieldRow(page: Page, label: string): Locator {
  return assistant(page).getByRole("row", { name: `Field ${label}` });
}

async function human(request: APIRequestContext, sessionId: string, action: string, body: object) {
  const response = await request.post(`${E2E}/human/${sessionId}/${action}`, { data: body });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function valueOf(request: APIRequestContext, sessionId: string, selector: string): Promise<string> {
  return (await human(request, sessionId, "value", { selector })).value as string;
}

async function checkedOf(request: APIRequestContext, sessionId: string, selector: string): Promise<boolean> {
  return (await human(request, sessionId, "checked", { selector })).checked as boolean;
}

async function siteStats(request: APIRequestContext): Promise<{ submissions: string[]; page_loads: Record<string, number> }> {
  return (await request.get(`${E2E}/site`)).json();
}

test("fill an application form with the assistant while staying in control", async ({ page, request }) => {
  const email = `e2e-assistant-${Date.now()}@example.com`;
  await register(page, email);
  const token = await apiToken(request, email);
  const headers = { Authorization: `Bearer ${token}` };
  await confirmResume(request, token);

  // --- An application whose page is the (fake) employer's form ------------------------------------------
  const applicationId = await startApplication(page, "Platform Engineer");
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

  // --- Nothing opens until the user starts the assistant ----------------------------------------------------
  const panel = assistant(page);
  await expect(panel.getByRole("heading", { level: 2, name: "Browser assistant" })).toBeVisible();
  await expect(panel.getByText(/JobPilot fills forms; you remain in control/)).toBeVisible();
  await expect(panel.getByText(/never submits your application/)).toBeVisible();
  expect((await siteStats(request)).page_loads["/apply"] ?? 0).toBe(0);

  // --- Start: the page is opened and its fields are discovered ------------------------------------------------
  await panel.getByRole("button", { name: "Start browser assistant" }).click();
  const status = panel.getByTestId("assistant-status");
  await expect(status).toContainText(/Needs your|Running/, { timeout: 60_000 });
  await expect(panel.getByText(/Found 24 fields/)).toBeVisible();
  await expect(panel.getByText("Application form").or(panel.getByText("Apply - Example GmbH"))).toBeVisible();
  const sessions = await (await request.get(`${API}/applications/${applicationId}/automation`, { headers })).json();
  const sessionId = sessions.items[0].id as string;
  expect((await siteStats(request)).page_loads["/apply"]).toBe(1);

  // --- Discovered fields, their questions and what is approved ---------------------------------------------------
  const table = panel.getByRole("table", { name: "Form fields" });
  await expect(table).toBeVisible();
  const salary = fieldRow(page, "Expected annual salary (EUR)");
  await expect(salary.getByText("Ready", { exact: true })).toBeVisible();
  await expect(salary.getByText(/Will fill \(from your application data\)/)).toBeVisible();
  const why = fieldRow(page, "Why do you want to work for us?");
  await expect(why.getByText("Needs your input", { exact: true })).toBeVisible();
  const privacy = fieldRow(page, "I agree to the privacy policy");
  await expect(privacy.getByText("Needs you", { exact: true })).toBeVisible();
  await expect(privacy.getByText(/Only you can give it: JobPilot never ticks these/)).toBeVisible();
  // The browser questions are the V0.9 questions of the application.
  const questions = (await (await request.get(`${API}/applications/${applicationId}/questions`, { headers })).json()).items;
  expect(questions.length).toBeGreaterThan(10);
  expect(questions.every((q: { source: string }) => q.source === "browser")).toBe(true);
  expect(await valueOf(request, sessionId, "#salary")).toBe(""); // nothing filled yet

  // --- Fill the approved answers: each is verified in the page ---------------------------------------------------
  await panel.getByRole("button", { name: "Fill approved answers" }).click();
  await expect(salary.getByText("Filled", { exact: true })).toBeVisible({ timeout: 30_000 });
  expect(await valueOf(request, sessionId, "#salary")).toBe("78000");
  expect(await valueOf(request, sessionId, "#start_date")).toBe("2027-01-01");
  expect(await checkedOf(request, sessionId, "input[name=authorized][value=yes]")).toBe(true);
  expect(await valueOf(request, sessionId, "#why")).toBe(""); // no approved answer: left empty
  expect(await checkedOf(request, sessionId, "#privacy")).toBe(false); // never ticked for the user
  expect(await checkedOf(request, sessionId, "#truth")).toBe(false);

  // --- The user edits a field in the browser, then takes control ---------------------------------------------------
  await human(request, sessionId, "type", { selector: "#salary", text: "99000" });
  await panel.getByRole("button", { name: "Take control" }).click();
  await expect(status).toContainText("User control");
  await expect(panel.getByText(/You are in control of the browser/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Fill approved answers" })).toHaveCount(0);

  // The assistant changes nothing while the user is in control -- not even when asked to.
  const refused = await request.post(`${API}/applications/${applicationId}/automation/${sessionId}/fill`, { headers });
  expect(refused.status()).toBe(409);
  await human(request, sessionId, "type", { selector: "#employer", text: "Acme GmbH" });
  await page.waitForTimeout(1200);
  expect(await valueOf(request, sessionId, "#salary")).toBe("99000");
  expect(await valueOf(request, sessionId, "#employer")).toBe("Acme GmbH");

  // --- Resume: the user's edits survive, only empty approved fields are filled --------------------------------
  await human(request, sessionId, "type", { selector: "#phone", text: "" });
  await panel.getByRole("button", { name: "Resume assistant" }).click();
  await expect(status).not.toContainText("User control", { timeout: 30_000 });
  await expect(salary.getByText("You changed this", { exact: true })).toBeVisible();
  expect(await valueOf(request, sessionId, "#salary")).toBe("99000");
  expect(await valueOf(request, sessionId, "#employer")).toBe("Acme GmbH");

  // The user can ask for one field to be written again, after confirming.
  await salary.getByRole("button", { name: "Fill Expected annual salary (EUR) again" }).click();
  await expect(salary.getByText("Replace what you typed with the approved answer?")).toBeVisible();
  await salary.getByRole("button", { name: "Keep mine" }).click();
  expect(await valueOf(request, sessionId, "#salary")).toBe("99000");

  // --- A file is attached only after "Use this file" ----------------------------------------------------------------
  const cv = fieldRow(page, "Resume / CV");
  await expect(cv.getByText(/This field appears to request:/)).toContainText("Resume / CV");
  await expect(cv.getByText("Tailored CV v1")).toBeVisible();
  expect((await human(request, sessionId, "files", { selector: "#cv" })).files).toEqual([]);
  await cv.getByRole("button", { name: "Use this file for Resume / CV" }).click();
  await expect(cv.getByText("Chosen:")).toBeVisible();
  await panel.getByRole("button", { name: "Fill approved answers" }).click();
  await expect(cv.getByText("Filled", { exact: true })).toBeVisible({ timeout: 30_000 });
  const attached = (await human(request, sessionId, "files", { selector: "#cv" })).files as [string, number][];
  expect(attached).toHaveLength(1);
  expect(attached[0]?.[0]).toMatch(/\.docx$/);
  expect((await human(request, sessionId, "files", { selector: "#cover" })).files).toEqual([]); // not chosen

  // --- The user does what only they can: the consent ----------------------------------------------------------------
  await human(request, sessionId, "check", { selector: "#privacy" });
  await panel.getByRole("button", { name: "Rediscover fields" }).click();
  await panel.getByRole("button", { name: "Fill approved answers" }).click();
  await expect(status).toBeVisible();
  expect(await checkedOf(request, sessionId, "#privacy")).toBe(true);

  // --- A live view, and the final step stays the user's ---------------------------------------------------------------
  await panel.getByText("Live view").click();
  await panel.getByRole("button", { name: "Show view" }).click();
  await expect(panel.getByAltText("What the browser shows now")).toBeVisible();
  await expect(panel.getByText(/does not submit applications. Please submit manually/)).toBeVisible();
  await expect(panel.getByRole("button", { name: /submit|apply|send application/i })).toHaveCount(0);

  await panel.getByRole("button", { name: "Stop assistant" }).click();
  await expect(panel.getByText(/no longer touches the browser/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Start browser assistant" })).toBeEnabled();

  // The employer's "Submit application" button was never pressed, and nothing was submitted.
  expect((await siteStats(request)).submissions).toEqual([]);

  // The server agrees with what the page showed.
  const final = await (await request.get(`${API}/applications/${applicationId}/automation/${sessionId}`, { headers })).json();
  expect(final.status).toBe("STOPPED");
  expect(final.submit_detected).toBe(true);
  const text = JSON.stringify(final) + JSON.stringify(sessions);
  expect(text).not.toMatch(/cookie|ws:\/\/|devtools|password/i);
});

test("a CAPTCHA stops the assistant and hands the browser to the user", async ({ page, request }) => {
  const email = `e2e-assistant-captcha-${Date.now()}@example.com`;
  await register(page, email);
  const token = await apiToken(request, email);
  const headers = { Authorization: `Bearer ${token}` };

  const applicationId = await startApplication(page, "Security Analyst");
  expect(applicationId).toBeTruthy();
  const panel = assistant(page);
  await panel.getByRole("button", { name: "Start browser assistant" }).click();

  // The assistant stops at the security check: it does not solve it or work around it.
  const status = panel.getByTestId("assistant-status");
  await expect(status).toContainText("Blocked", { timeout: 60_000 });
  await expect(panel.getByRole("alert")).toContainText("does not solve or work around");
  await expect(panel.getByRole("button", { name: "Fill approved answers" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Resume assistant" })).toHaveCount(0);
  const sessions = await (await request.get(`${API}/applications/${applicationId}/automation`, { headers })).json();
  const sessionId = sessions.items[0].id as string;
  expect(sessions.items[0].status).toBe("BLOCKED");
  const fields = await (await request.get(`${API}/applications/${applicationId}/automation/${sessionId}/fields`, { headers })).json();
  expect(fields.items).toEqual([]);

  // The user takes over, completes the check themselves, and hands the browser back.
  await panel.getByRole("button", { name: "Take control" }).click();
  await expect(status).toContainText("User control");
  await panel.getByRole("button", { name: "Resume assistant" }).click();
  await expect(status).not.toContainText("User control", { timeout: 30_000 });
  await expect(fieldRow(page, "Email address")).toBeVisible();
  await expect(panel.getByRole("alert")).toHaveCount(0);

  await panel.getByRole("button", { name: "Stop and close the browser" }).click();
  await expect(panel.getByText(/no longer touches the browser/)).toBeVisible();
  expect((await siteStats(request)).submissions).toEqual([]);
});
