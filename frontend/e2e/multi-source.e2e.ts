import { expect, test, type Page } from "@playwright/test";

/**
 * V0.4 in a real browser: one vacancy published on Greenhouse and Lever
 * becomes one canonical job with both original links; a same-titled Munich
 * posting stays separate; saved state survives a repeated discovery that
 * creates no duplicates.
 */

const email = `e2e-${Date.now()}@example.com`;
const password = "supersecret123";

async function register(page: Page) {
  await page.goto("/register");
  await page.getByLabel("First name").fill("Erika");
  await page.getByLabel("Last name").fill("Mustermann");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function discover(page: Page) {
  await page.getByRole("button", { name: "Discover jobs" }).click();
  await expect(page.getByText(/Discovery finished/)).toBeVisible({ timeout: 60_000 });
}

function sapCards(page: Page) {
  return page.getByRole("article", { name: "Senior SAP ABAP Developer" });
}

test("one vacancy on two sources is one job with both source links", async ({ page }) => {
  await register(page);
  await page.getByRole("button", { name: "Log out" }).click();
  await login(page);

  await page.goto("/jobs");
  await expect(page.getByText("No jobs found")).toBeVisible();
  await discover(page);
  await expect(page.getByText(/1 posting was recognized as the same job on another source/)).toBeVisible();

  // Cologne (Greenhouse + Lever) is one card; Munich (Lever only) stays separate.
  await expect(sapCards(page)).toHaveCount(2);
  const cologne = sapCards(page).filter({ hasText: "Cologne" });
  await expect(cologne).toHaveCount(1);
  await expect(cologne.getByText("Sources: Greenhouse + Lever")).toBeVisible();
  const munich = sapCards(page).filter({ hasText: "München" });
  await expect(munich).toHaveCount(1);
  await expect(munich.getByText("via Lever")).toBeVisible();

  // Details: canonical data plus every source with its original link.
  await cologne.getByRole("link", { name: "Details" }).click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Senior SAP ABAP Developer" })
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "JobPilot data" })).toBeVisible();
  const sources = page.getByRole("list", { name: "Found on" });
  await expect(sources.getByRole("listitem")).toHaveCount(2);
  await expect(
    sources.getByRole("link", { name: "View original posting on Greenhouse" })
  ).toHaveAttribute("href", "https://job-boards.greenhouse.io/example/jobs/7001");
  await expect(
    sources.getByRole("link", { name: "View original posting on Lever" })
  ).toHaveAttribute("href", "https://jobs.lever.co/example/c0ffee00-0000-4000-8000-000000000001");
  await expect(sources.getByRole("listitem", { name: "Lever" })).toContainText("in Köln");
  await expect(sources.getByRole("listitem", { name: "Greenhouse" })).toContainText("Preferred");
  const jobUrl = page.url();

  // Save, and the saved state survives a reload.
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("button", { name: "Saved ✓" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Saved ✓" })).toBeVisible();

  // Discover again: nothing new, no duplicate, still saved, same job id.
  await page.goto("/jobs");
  await expect(sapCards(page)).toHaveCount(2);
  await discover(page);
  await expect(page.getByText(/\(0 new, 0 updated\)/)).toBeVisible();
  await expect(sapCards(page)).toHaveCount(2);
  await expect(page.getByRole("heading", { name: /Your jobs/ })).toContainText("(2)");
  const cologneAgain = sapCards(page).filter({ hasText: "Cologne" });
  await expect(cologneAgain.getByRole("button", { name: "Saved ✓" })).toBeVisible();
  await cologneAgain.getByRole("link", { name: "Details" }).click();
  await expect(page).toHaveURL(jobUrl);
  await expect(page.getByRole("button", { name: "Saved ✓" })).toBeVisible();

  // Source filter: the merged job is found under either source.
  for (const source of ["greenhouse", "lever"]) {
    await page.goto("/jobs");
    await page.getByLabel("Source").selectOption(source);
    await page.getByRole("button", { name: "Filter my jobs" }).click();
    await expect(sapCards(page).filter({ hasText: "Cologne" })).toHaveCount(1);
  }
});
