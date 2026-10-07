import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * V0.7 in a real browser: starting an application is explicit; the
 * Application Workspace stores application-specific preparation data (salary
 * expectation, whether to mention it, availability, notice period, language,
 * work authorization, notes) that survives a reload; the application keeps
 * pointing at the resume it started with; the original posting opens; and
 * "Start Application" for the same job again is "Continue Application".
 *
 * Named to run after v06-matching.e2e.ts (the first spec discovers the mock
 * jobs in the freshly migrated E2E database; later ones only read them).
 */

const API = "http://127.0.0.1:8000/api/v1";
const email = `e2e-application-${Date.now()}@example.com`;
const password = "supersecret123";

const GREENHOUSE_URL = "https://job-boards.greenhouse.io/example/jobs/7001";

async function register(page: Page, address = email) {
  await page.goto("/register");
  await page.getByLabel("First name").fill("Erika");
  await page.getByLabel("Last name").fill("Mustermann");
  await page.getByLabel("Email").fill(address);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function apiToken(request: APIRequestContext, address = email): Promise<string> {
  const response = await request.post(`${API}/auth/login`, { data: { email: address, password } });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}

/** A profile through the real API (the forms have their own tests), so there is a match to see. */
async function setUpProfile(request: APIRequestContext, token: string) {
  const headers = { Authorization: `Bearer ${token}` };
  const profile = await request.put(`${API}/profile`, {
    headers,
    data: {
      current_job_title: "SAP ABAP Developer",
      years_of_experience: 8,
      seniority_level: "senior",
      location: "Köln",
    },
  });
  expect(profile.ok()).toBeTruthy();
  const preferences = await request.put(`${API}/preferences`, {
    headers,
    data: {
      preferred_locations: [{ location: "Cologne" }],
      work_models: ["hybrid"],
      employment_types: ["full_time"],
      desired_job_titles: [{ title: "SAP ABAP Developer", priority: "primary" }],
    },
  });
  expect(preferences.ok()).toBeTruthy();
}

async function uploadResume(request: APIRequestContext, token: string, name: string) {
  const response = await request.post(`${API}/resumes`, {
    headers: { Authorization: `Bearer ${token}` },
    // Upload only validates the type and size; analysis (AI) isn't involved.
    multipart: {
      file: { name, mimeType: "application/pdf", buffer: Buffer.from(`%PDF-1.4 ${name}`) },
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as { id: string; version_number: number };
}

function cologneCard(page: Page) {
  return page
    .getByRole("article", { name: "Senior SAP ABAP Developer" })
    .filter({ hasText: "Cologne" });
}

test("start an application, prepare it, reload, open the posting, and continue it later", async ({
  page,
  request,
}) => {
  await register(page);
  const token = await apiToken(request);
  const headers = { Authorization: `Bearer ${token}` };
  await setUpProfile(request, token);
  const firstResume = await uploadResume(request, token, "cv-first.pdf");

  // --- Discover jobs, open one, see its match -------------------------------------------------
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Discover jobs" }).click();
  await expect(page.getByText(/Discovery finished/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("article", { name: "Senior SAP ABAP Developer" })).toHaveCount(2);
  // Discovering and listing created no application.
  await expect(page.getByText(/Application: /)).toHaveCount(0);
  expect((await (await request.get(`${API}/applications`, { headers })).json()).total).toBe(0);

  await cologneCard(page).getByRole("link", { name: "Details" }).click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/);
  const jobUrl = page.url();
  await page.getByRole("button", { name: "Calculate match" }).click();
  await expect(page.getByLabel("Overall match")).toHaveText(/^\d+%$/);

  // Calculating a match is not starting an application.
  await expect(page.getByRole("button", { name: "Start Application" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Continue Application" })).toHaveCount(0);
  await expect(page.getByText(/Nothing is submitted or sent to Example GmbH/)).toBeVisible();
  expect((await (await request.get(`${API}/applications`, { headers })).json()).total).toBe(0);

  // --- Start Application -> the workspace ----------------------------------------------------------
  await page.getByRole("button", { name: "Start Application" }).click();
  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
  const workspaceUrl = page.url();
  const applicationId = workspaceUrl.split("/").pop()!;

  await expect(
    page.getByRole("heading", { level: 1, name: "Senior SAP ABAP Developer" })
  ).toBeVisible();
  await expect(page.getByLabel("Application status")).toHaveText("In preparation");
  await expect(page.getByText(/Starting an application does not submit it/)).toBeVisible();

  // The job, as it was when the application started, with where it was found.
  const job = page.getByRole("heading", { name: "Job", exact: true }).locator("xpath=ancestor::div[1]");
  await expect(job.getByText("Example GmbH")).toBeVisible();
  await expect(job.getByText("Greenhouse (example)")).toBeVisible();
  await expect(job.getByRole("link", { name: "View original posting ↗" })).toHaveAttribute(
    "href",
    GREENHOUSE_URL
  );

  // The existing match is shown, not recalculated.
  const match = page.getByRole("heading", { name: "Your match" }).locator("xpath=ancestor::div[1]");
  await expect(match.getByLabel("Overall match")).toHaveText(/^\d+%$/);
  await expect(match.getByLabel("Job match")).toHaveText(/^\d+%$/);
  await expect(match.getByLabel("Preference match")).toHaveText(/^\d+%$/);

  // The resume found for the user, and nothing invented for the fields.
  await expect(page.getByText("Base resume")).toBeVisible();
  await expect(page.getByText("Version 1 · cv-first.pdf")).toBeVisible();
  await expect(page.getByLabel("Salary expectation")).toHaveValue("");
  await expect(page.getByLabel("Currency")).toHaveValue("");
  await expect(page.getByLabel(/Mention salary/)).toHaveValue("");
  await expect(page.getByLabel("Available from")).toHaveValue("");
  await expect(page.getByLabel("Notice period", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Application language")).toHaveValue("");
  await expect(page.getByLabel("Progress summary")).toHaveText("2 of 6 steps complete");
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();

  // --- Prepare the application ---------------------------------------------------------------------------
  await page.getByLabel("Salary expectation").fill("78000");
  await page.getByLabel("Currency").fill("eur");
  await expect(page.getByLabel("Currency")).toHaveValue("EUR");
  await page.getByLabel(/Mention salary/).selectOption("yes");
  await page.getByLabel("Available from").fill("2027-01-01");
  await page.getByLabel("Notice period", { exact: true }).fill("3");
  await page.getByLabel("Notice period unit").selectOption("months");
  await page.getByLabel("Application language").selectOption("de");
  await page.getByLabel("Work authorization (optional)").selectOption("authorized");
  await page.getByLabel("Private notes").fill("Referral from Alex. Ask about the remote policy.");
  await page.getByRole("button", { name: "Save changes" }).click();

  await expect(page.getByText("Application saved.")).toBeVisible();
  await expect(page.getByLabel("Progress summary")).toHaveText("6 of 6 steps complete");
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();

  // --- Reload: everything persisted ---------------------------------------------------------------------------
  await page.reload();
  await expect(
    page.getByRole("heading", { level: 1, name: "Senior SAP ABAP Developer" })
  ).toBeVisible();
  await expect(page.getByLabel("Salary expectation")).toHaveValue("78000");
  await expect(page.getByLabel("Currency")).toHaveValue("EUR");
  await expect(page.getByLabel(/Mention salary/)).toHaveValue("yes");
  await expect(page.getByLabel("Available from")).toHaveValue("2027-01-01");
  await expect(page.getByLabel("Notice period", { exact: true })).toHaveValue("3");
  await expect(page.getByLabel("Notice period unit")).toHaveValue("months");
  await expect(page.getByLabel("Application language")).toHaveValue("de");
  await expect(page.getByLabel("Work authorization (optional)")).toHaveValue("authorized");
  await expect(page.getByLabel("Private notes")).toHaveValue(
    "Referral from Alex. Ask about the remote policy."
  );
  await expect(page.getByLabel("Progress summary")).toHaveText("6 of 6 steps complete");

  // Application data never leaks into the profile or preferences.
  const preferences = await (await request.get(`${API}/preferences`, { headers })).json();
  expect(preferences.desired_salary).toBeNull();
  expect(preferences.minimum_salary).toBeNull();
  expect(preferences.salary_currency).toBeNull();

  // --- A server-side rule is shown next to its field -------------------------------------------------------------------
  await page.getByLabel("Available from").fill("1999-01-01");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText(/Available from must be a realistic date/)).toBeVisible();
  await expect(
    page.getByRole("form", { name: "Application preparation" }).getByRole("alert")
  ).toHaveText("Please fix the highlighted fields.");
  await page.getByLabel("Available from").fill("2027-01-01");
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();

  // --- A newer resume doesn't change the application's ---------------------------------------------------------------
  const secondResume = await uploadResume(request, token, "cv-second.pdf");
  expect(secondResume.version_number).toBe(2);
  await page.reload();
  await expect(page.getByText("Version 1 · cv-first.pdf")).toBeVisible();
  await expect(page.getByLabel("Resume version")).toHaveValue(firstResume.id);
  await expect(page.getByLabel("Resume version").locator("option")).toHaveCount(2);

  // --- Open the original posting (the external host is stubbed: no real network) --------------------------------------
  await page.context().route("https://job-boards.greenhouse.io/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Posting 7001</title><h1>Original posting 7001</h1>",
    })
  );
  const popupPromise = page.context().waitForEvent("page");
  await page.getByRole("link", { name: "View original posting ↗" }).click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  expect(popup.url()).toBe(GREENHOUSE_URL);
  await expect(popup.getByRole("heading", { name: "Original posting 7001" })).toBeVisible();
  await popup.close();
  // The workspace is still open and unchanged.
  await expect(page).toHaveURL(workspaceUrl);

  // --- Navigate away and come back: Continue Application opens the same application ------------------------------
  await page.goto("/jobs");
  const chip = cologneCard(page).getByRole("link", { name: "Application: In preparation" });
  await expect(chip).toHaveAttribute("href", `/applications/${applicationId}`);
  await page.goto(jobUrl);
  await expect(page.getByRole("button", { name: "Start Application" })).toHaveCount(0);
  await page.getByRole("link", { name: "Continue Application" }).click();
  await expect(page).toHaveURL(workspaceUrl);
  await expect(page.getByLabel("Salary expectation")).toHaveValue("78000");

  // There is exactly one application, however often it was opened or started.
  const again = await request.post(`${API}/jobs/${jobUrl.split("/").pop()}/application`, {
    headers,
    data: {},
  });
  expect(again.status()).toBe(200);
  expect((await again.json()).id).toBe(applicationId);
  const listed = await (await request.get(`${API}/applications`, { headers })).json();
  expect(listed.total).toBe(1);
  expect(listed.items[0].id).toBe(applicationId);

  // The other (Munich) job has none: nothing was created implicitly.
  await page.goto("/jobs");
  const munich = page
    .getByRole("article", { name: "Senior SAP ABAP Developer" })
    .filter({ hasText: "München" });
  await expect(munich.getByText(/Application: /)).toHaveCount(0);
  await munich.getByRole("link", { name: "Details" }).click();
  await expect(page.getByRole("button", { name: "Start Application" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Continue Application" })).toHaveCount(0);
});

test("an application is private to its user", async ({ page, request }) => {
  const owner = `e2e-owner-${Date.now()}@example.com`;
  const other = `e2e-other-${Date.now()}@example.com`;
  await register(page, owner);
  const ownerToken = await apiToken(request, owner);
  const ownerHeaders = { Authorization: `Bearer ${ownerToken}` };

  // The owner discovers the jobs and starts an application through the real API.
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Discover jobs" }).click();
  await expect(page.getByText(/Discovery finished/)).toBeVisible({ timeout: 60_000 });
  const jobs = await (await request.get(`${API}/jobs`, { headers: ownerHeaders })).json();
  const jobId = jobs.items[0].id as string;
  const started = await request.post(`${API}/jobs/${jobId}/application`, {
    headers: ownerHeaders,
    data: { notes: "Only for me." },
  });
  expect(started.status()).toBe(201);
  const applicationId = (await started.json()).id as string;

  // Another user can neither read nor change it, nor start one for the owner's job.
  const registered = await request.post(`${API}/auth/register`, {
    data: { email: other, password, first_name: "Max", last_name: "Muster" },
  });
  expect(registered.status()).toBe(201);
  const otherHeaders = { Authorization: `Bearer ${(await registered.json()).access_token}` };
  expect((await request.get(`${API}/applications/${applicationId}`, { headers: otherHeaders })).status()).toBe(404);
  const patched = await request.patch(`${API}/applications/${applicationId}`, {
    headers: otherHeaders,
    data: { notes: "hijacked" },
  });
  expect(patched.status()).toBe(404);
  expect((await request.post(`${API}/jobs/${jobId}/application`, { headers: otherHeaders, data: {} })).status()).toBe(404);
  expect((await (await request.get(`${API}/applications`, { headers: otherHeaders })).json()).total).toBe(0);

  // And in the browser, as the other user, the workspace is simply not there.
  await page.getByRole("button", { name: "Log out" }).click();
  await page.goto("/login");
  await page.getByLabel("Email").fill(other);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await page.goto(`/applications/${applicationId}`);
  await expect(page.getByRole("main").getByRole("alert")).toHaveText(
    "This application doesn't exist or isn't yours."
  );
  await expect(page.getByText("Only for me.")).toHaveCount(0);

  // The owner's data is untouched.
  const mine = await (await request.get(`${API}/applications/${applicationId}`, { headers: ownerHeaders })).json();
  expect(mine.notes).toBe("Only for me.");
});
