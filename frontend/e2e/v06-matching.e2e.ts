import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * V0.6 in a real browser: deterministic match calculation for discovered
 * jobs, the job-list badge (overall vs. job vs. preference match), the
 * explainable details panel, staleness after a profile change, and the
 * graceful "AI not configured" state (the E2E backend has no AI key).
 *
 * Named to run after multi-source.e2e.ts, which expects to be the first to
 * discover the mock jobs in the freshly migrated E2E database.
 */

const API = "http://127.0.0.1:8000/api/v1";
const email = `e2e-match-${Date.now()}@example.com`;
const password = "supersecret123";

async function register(page: Page) {
  await page.goto("/register");
  await page.getByLabel("First name").fill("Max");
  await page.getByLabel("Last name").fill("Muster");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function apiToken(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${API}/auth/login`, {
    data: { email, password },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}

/** Profile and preferences through the real API (the forms have their own tests). */
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
  for (const [name, proficiency] of [
    ["German", "native"],
    ["English", "fluent"],
  ]) {
    const language = await request.post(`${API}/profile/languages`, {
      headers,
      data: { name, proficiency },
    });
    expect(language.ok()).toBeTruthy();
  }
  const preferences = await request.put(`${API}/preferences`, {
    headers,
    data: {
      preferred_locations: [{ location: "Cologne" }],
      relocation: "no",
      work_models: ["hybrid", "onsite"],
      employment_types: ["full_time"],
      desired_job_titles: [{ title: "SAP ABAP Developer", priority: "primary" }],
    },
  });
  expect(preferences.ok()).toBeTruthy();
}

function cards(page: Page) {
  return page.getByRole("article", { name: "Senior SAP ABAP Developer" });
}

test("calculate, explain and refresh a job match", async ({ page, request }) => {
  await register(page);
  const token = await apiToken(request);
  await setUpProfile(request, token);

  await page.goto("/jobs");
  await page.getByRole("button", { name: "Discover jobs" }).click();
  await expect(page.getByText(/Discovery finished/)).toBeVisible({ timeout: 60_000 });
  await expect(cards(page)).toHaveCount(2);

  // No score is shown before one exists.
  await expect(page.getByText("Match not calculated")).toHaveCount(2);

  await page.getByRole("button", { name: "Calculate matches" }).click();
  await expect(page.getByText("Calculated 2 matches.")).toBeVisible();
  const cologne = cards(page).filter({ hasText: "Cologne" });
  await expect(cologne.getByText(/^\d+% Overall match$/)).toBeVisible();
  await expect(cologne.getByText(/^Job match \d+% · Preference match \d+%$/)).toBeVisible();
  await expect(page.getByText("Match not calculated")).toHaveCount(0);

  // Sorting by best match keeps both jobs, the Cologne one first.
  await page.getByLabel("Sort").selectOption("match");
  await expect(cards(page).first()).toContainText("Cologne");

  // Details: the explainable match.
  await cologne.getByRole("link", { name: "Details" }).click();
  await expect(page.getByRole("heading", { name: "Your match" })).toBeVisible();
  await expect(page.getByLabel("Overall match")).toHaveText(/^\d+%$/);
  await expect(page.getByLabel("Job match")).toHaveText(/^\d+%$/);
  await expect(page.getByLabel("Preference match")).toHaveText(/^\d+%$/);
  await expect(page.getByLabel("Recommendation")).toBeVisible();
  const strong = page.getByRole("region", { name: "Why this matches" });
  await expect(strong.getByText("8 years of experience (5+ asked)")).toBeVisible();
  await expect(strong.getByText("Your current role: SAP ABAP Developer")).toBeVisible();
  await expect(strong.getByText("German (you: native)")).toBeVisible();
  // "SAP" names many products: shown as unknown, never guessed or scored.
  const unknown = page.getByRole("region", { name: "Unknown" });
  await expect(unknown.getByText('"SAP" is mentioned, but the term is ambiguous')).toBeVisible();

  await page.getByText("Show detailed evidence").click();
  await expect(page.getByText(/matching_v1 · taxonomy_v1 · job_extractor_v1/)).toBeVisible();

  // AI is optional: without a provider the deterministic explanation stands.
  await page.getByRole("button", { name: "Explain in plain words (AI)" }).click();
  await expect(page.getByText(/AI explanations are not available/)).toBeVisible();
  await expect(strong).toBeVisible();

  // A profile change makes the result stale until it is recalculated.
  const skill = await request.post(`${API}/profile/skills`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: "SQL" },
  });
  expect(skill.ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByText(/Out of date: your profile/)).toBeVisible();
  await page.getByRole("button", { name: "Recalculate" }).click();
  await expect(page.getByText(/Out of date/)).toHaveCount(0);
  await expect(page.getByLabel("Overall match")).toHaveText(/^\d+%$/);
});
