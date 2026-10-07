import { readFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

/**
 * V0.13 in a real browser, against the real backend and database: privacy & security.
 *
 *  - the frontend sends its security headers (CSP without third-party origins);
 *  - logout ends the session on the server: the old token is refused afterwards;
 *  - Settings > Privacy & data shows what is stored, has no optional cookies, records the
 *    external AI processing choice, downloads a ZIP with the user's data;
 *  - account deletion needs the password and the exact phrase, removes the account, and the
 *    credentials stop working.
 */

const API = "http://127.0.0.1:8000/api/v1";
const password = "supersecret123";

function email(tag: string): string {
  return `v13-${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function register(page: Page, address: string) {
  await page.goto("/register");
  await page.getByLabel("First name").fill("Priya");
  await page.getByLabel("Last name").fill("Vacy");
  await page.getByLabel("Email").fill(address);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function storedToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => window.localStorage.getItem("jobpilot_access_token"));
  expect(token).toBeTruthy();
  return token as string;
}

test("the frontend sends strict security headers", async ({ request }) => {
  const response = await request.get("/login");
  const csp = response.headers()["content-security-policy"];
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).not.toContain("unsafe-eval");
  expect(response.headers()["x-frame-options"]).toBe("DENY");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["x-powered-by"]).toBeUndefined();
});

test("logging out ends the session on the server", async ({ page, request }) => {
  await register(page, email("logout"));
  const token = await storedToken(page);
  const headers = { Authorization: `Bearer ${token}` };
  expect((await request.get(`${API}/users/me`, { headers })).status()).toBe(200);

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect
    .poll(async () => (await request.get(`${API}/users/me`, { headers })).status())
    .toBe(401);
});

test("privacy settings: my data, AI choice, cookie preferences and download", async ({ page }) => {
  await register(page, email("privacy"));
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

  // My data
  await expect(page.getByTestId("data-category-account")).toContainText("1 records");
  await expect(page.getByText(/legal review/)).toBeVisible();

  // Cookie preferences: nothing optional, and no banner anywhere.
  await expect(page.getByTestId("no-optional-cookies")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // External AI processing: switch off, recorded, switch on again.
  const toggle = page.getByRole("checkbox", { name: "Allow external AI processing" });
  await expect(toggle).toBeChecked();
  // A controlled checkbox: it changes when the server has recorded the choice.
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByTestId("consent-history")).toContainText("switched off");
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Allow external AI processing" })).not.toBeChecked();
  await page.getByRole("checkbox", { name: "Allow external AI processing" }).click();
  await expect(page.getByRole("checkbox", { name: "Allow external AI processing" })).toBeChecked();
  await expect(page.getByTestId("consent-history")).toContainText("allowed");

  // Download my data: a real ZIP with the JSON document.
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download my data" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^jobpilot-data-\d{4}-\d{2}-\d{2}\.zip$/);
  const file = await download.path();
  const bytes = readFileSync(file!);
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
  expect(bytes.includes(Buffer.from("jobpilot-data.json"))).toBeTruthy();
});

test("account deletion needs explicit confirmation and removes the account", async ({ page, request }) => {
  const address = email("delete");
  await register(page, address);
  const token = await storedToken(page);
  await page.goto("/settings");

  await page.getByRole("button", { name: "I want to delete my account" }).click();
  const submit = page.getByRole("button", { name: "Permanently delete my account" });
  await expect(submit).toBeDisabled();

  // Wrong password: refused, account kept.
  await page.getByLabel("Your password").fill("not-my-password");
  await page.getByLabel(/to confirm/).fill("DELETE MY ACCOUNT");
  await submit.click();
  await expect(page.getByText("The password is not correct.")).toBeVisible();
  expect((await request.get(`${API}/users/me`, { headers: { Authorization: `Bearer ${token}` } })).status()).toBe(200);

  // Right password and phrase: deleted.
  await page.getByLabel("Your password").fill(password);
  await submit.click();
  await expect(page).toHaveURL(/\/login\?account=deleted/);
  await expect(page.getByText("Your account and all of its data have been deleted.")).toBeVisible();

  expect((await request.get(`${API}/users/me`, { headers: { Authorization: `Bearer ${token}` } })).status()).toBe(401);
  const login = await request.post(`${API}/auth/login`, { data: { email: address, password } });
  expect(login.status()).toBe(401);
});
