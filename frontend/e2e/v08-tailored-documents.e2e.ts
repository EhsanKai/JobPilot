import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/**
 * V0.8 in a real browser, against the real backend and database: a tailored
 * CV and an Anschreiben are generated for an Application from the user's
 * confirmed resume, validated, versioned, and still there after a reload.
 *
 * The AI is the deterministic stub of backend/e2e/serve.py (it restates the
 * facts it is shown); everything around it -- context, structured output,
 * truth validation, versioning, storage, authorization, the UI -- is the real
 * thing. Named to run after v07-application-workspace.e2e.ts.
 */

const API = "http://127.0.0.1:8000/api/v1";
const email = `e2e-documents-${Date.now()}@example.com`;
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

/** A real resume file, analyzed (stub AI), reviewed and confirmed through the real API. */
async function confirmResume(request: APIRequestContext, token: string): Promise<string> {
  const headers = { Authorization: `Bearer ${token}` };
  const upload = await request.post(`${API}/resumes`, {
    headers,
    multipart: {
      file: { name: "alex-example.pdf", mimeType: "application/pdf", buffer: readFileSync(RESUME_PDF) },
    },
  });
  expect(upload.status()).toBe(201);
  const resume = (await upload.json()) as { id: string };

  const process = await request.post(`${API}/resumes/${resume.id}/process`, { headers, data: {} });
  expect(process.status()).toBe(202);
  await expect
    .poll(async () => {
      const profile = await (await request.get(`${API}/resumes/${resume.id}/profile`, { headers })).json();
      return profile.profile?.status ?? profile.latest_attempt?.status;
    })
    .toBe("needs_review");
  expect((await request.post(`${API}/resumes/${resume.id}/profile/accept-pending`, { headers })).ok()).toBeTruthy();
  expect((await request.post(`${API}/resumes/${resume.id}/profile/confirm`, { headers })).ok()).toBeTruthy();
  return resume.id;
}

function panel(page: Page, name: string): Locator {
  return page.getByRole("heading", { name, exact: true }).locator("xpath=ancestor::section[1]");
}

test("generate a tailored CV and Anschreiben, reload, and regenerate as a new version", async ({
  page,
  request,
  browser,
}) => {
  await register(page, email);
  const token = await apiToken(request, email);
  const headers = { Authorization: `Bearer ${token}` };
  await confirmResume(request, token);

  // --- Open a job and start the application ------------------------------------------------------
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Discover jobs" }).click();
  await expect(page.getByText(/Discovery finished/)).toBeVisible({ timeout: 60_000 });
  await page
    .getByRole("article", { name: "Senior SAP ABAP Developer" })
    .filter({ hasText: "Cologne" })
    .getByRole("link", { name: "Details" })
    .click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/);
  await page.getByRole("button", { name: "Start Application" }).click();
  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
  const applicationId = page.url().split("/").pop()!;
  await expect(page.getByText("Version 1 · alex-example.pdf")).toBeVisible();

  // --- Nothing is generated yet, and nothing can be until the language is chosen -------------
  const cv = panel(page, "Tailored CV");
  const letter = panel(page, "Cover letter (Anschreiben)");
  await expect(page.getByRole("heading", { level: 2, name: "Documents" })).toBeVisible();
  await expect(cv.getByText(/Status: Not generated/)).toBeVisible();
  await expect(letter.getByText(/Status: Not generated/)).toBeVisible();
  await expect(cv.getByText(/Select the application language first/)).toBeVisible();
  await expect(cv.getByRole("button", { name: "Generate CV" })).toBeDisabled();
  await expect(letter.getByRole("button", { name: "Generate Anschreiben" })).toBeDisabled();

  // --- Language and salary: the CV is ready; the letter waits for the salary decision --------
  await page.getByLabel("Application language").selectOption("de");
  await page.getByLabel("Salary expectation").fill("78000");
  await page.getByLabel("Currency").fill("eur");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Application saved.")).toBeVisible();

  await expect(page.getByText(/and written in German/)).toBeVisible();
  await expect(cv.getByRole("button", { name: "Generate CV" })).toBeEnabled();
  await expect(letter.getByText("A salary decision is needed first.")).toBeVisible();
  await expect(letter.getByRole("button", { name: "Generate Anschreiben" })).toBeDisabled();

  await page.getByLabel(/Mention salary/).selectOption("yes");
  // unsaved changes can't be generated from
  await expect(page.getByText(/You have unsaved changes/)).toBeVisible();
  await expect(cv.getByRole("button", { name: "Generate CV" })).toBeDisabled();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Application saved.")).toBeVisible();
  await expect(letter.getByRole("button", { name: "Generate Anschreiben" })).toBeEnabled();

  // --- Generate the tailored CV ------------------------------------------------------------------------
  await cv.getByRole("button", { name: "Generate CV" }).click();
  await expect(cv.getByText("✓ Generated")).toBeVisible();
  await expect(cv.locator('[data-status="generated"]')).toContainText("Version 1");
  await expect(cv.locator('[data-status="generated"]')).toContainText("Generated today");
  await expect(cv.getByText(/Checked against your resume \(resume version 1\)/)).toBeVisible();
  // generating the CV did not generate the letter
  await expect(letter.getByText(/Status: Not generated/)).toBeVisible();

  await cv.getByRole("button", { name: "View Tailored CV" }).click();
  const cvDocument = page.getByRole("region", { name: "Tailored CV, version 1" });
  await expect(cvDocument.getByText("Alex Example", { exact: true })).toBeVisible();
  await expect(cvDocument.getByText("SAP IS-U Developer — Example Energy GmbH")).toBeVisible();
  await expect(cvDocument.getByText("SAP ABAP Developer — Sample Consulting AG")).toBeVisible();
  await expect(
    cvDocument.getByText("Entwicklung und Wartung von SAP IS-U-Anwendungen für Meter-to-Cash-Prozesse")
  ).toBeVisible();
  await expect(cvDocument.getByText(/Reduzierung der Batch-Laufzeit um 30 %/)).toBeVisible();
  await expect(cvDocument.getByText(/Passed — every statement is backed by your resume/)).toBeVisible();
  await expect(cvDocument.getByText(/e2e-stub \(e2e-stub-1\) · tailored_cv_v1/)).toBeVisible();
  // only the resume's own skills; the job's technologies were never added
  const skills = cvDocument.getByRole("region", { name: "Skills" }).getByRole("listitem");
  await expect(skills).toHaveText(["SAP ABAP", "SAP IS-U", "SAP BTP", "SQL", "Git"]);
  await expect(cvDocument).not.toContainText("AWS");
  // a statement can be traced to the resume fact it came from
  await cvDocument.getByText("Sources (1)").first().click();
  await expect(cvDocument.getByText("SAP developer with experience in SAP IS-U and ABAP development for utility companies.")).toBeVisible();
  // the salary is never part of the CV
  await expect(cvDocument).not.toContainText("78");

  // --- Generate the Anschreiben -----------------------------------------------------------------------------
  await letter.getByRole("button", { name: "Generate Anschreiben" }).click();
  await expect(letter.getByText("✓ Generated")).toBeVisible();
  await expect(letter.locator('[data-status="generated"]')).toContainText("Version 1");
  await letter.getByRole("button", { name: "View Cover letter (Anschreiben)" }).click();
  const letterDocument = page.getByRole("region", { name: "Cover letter (Anschreiben), version 1" });
  await expect(letterDocument.getByText("Bewerbung als Senior SAP ABAP Developer")).toBeVisible();
  await expect(letterDocument.getByText("Sehr geehrte Damen und Herren,")).toBeVisible();
  await expect(
    letterDocument.getByText(/bewerbe ich mich auf die Position Senior SAP ABAP Developer bei Example GmbH/)
  ).toBeVisible();
  await expect(letterDocument.getByRole("group", { name: "Salary statement" })).toHaveText(
    "Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr."
  );
  await expect(letterDocument.getByText("Mit freundlichen Grüßen")).toBeVisible();
  await expect(letterDocument.getByText(/e2e-stub \(e2e-stub-1\) · cover_letter_v1/)).toBeVisible();

  // --- Reload: both documents are still there ------------------------------------------------------------
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "Documents" })).toBeVisible();
  await expect(cv.getByText("✓ Generated")).toBeVisible();
  await expect(cv.locator('[data-status="generated"]')).toContainText("Version 1");
  await expect(letter.getByText("✓ Generated")).toBeVisible();
  await expect(letter.locator('[data-status="generated"]')).toContainText("Version 1");
  await cv.getByRole("button", { name: "View Tailored CV" }).click();
  await expect(
    page.getByRole("region", { name: "Tailored CV, version 1" }).getByText("Alex Example", { exact: true })
  ).toBeVisible();

  // --- Regenerate the CV: a new version; the letter and version 1 are untouched ---------------------------
  await cv.getByRole("button", { name: "Regenerate Tailored CV" }).click();
  await expect(cv.locator('[data-status="generated"]')).toContainText("Version 2");
  await expect(letter.locator('[data-status="generated"]')).toContainText("Version 1");
  await cv.getByText(/History \(2 attempts\)/).click();
  await cv.getByRole("button", { name: "View Tailored CV version 1" }).click();
  await expect(page.getByRole("region", { name: "Tailored CV, version 1" })).toBeVisible();
  await cv.getByRole("button", { name: "View Tailored CV version 2" }).click();
  await expect(page.getByRole("region", { name: "Tailored CV, version 2" })).toBeVisible();

  // --- What the server holds ----------------------------------------------------------------------------------
  const list = await (await request.get(`${API}/applications/${applicationId}/documents`, { headers })).json();
  const summary = (list.items as { document_type: string; version_number: number; status: string; is_current: boolean }[])
    .map((item) => `${item.document_type}:${item.version_number}:${item.status}:${item.is_current}`)
    .sort();
  expect(summary).toEqual([
    "cover_letter:1:validated:true",
    "tailored_cv:1:validated:false",
    "tailored_cv:2:validated:true",
  ]);
  for (const item of list.items as { ai_provider: string; ai_model: string; prompt_version: string; validation_status: string }[]) {
    expect(item.ai_provider).toBe("e2e-stub");
    expect(item.ai_model).toBe("e2e-stub-1");
    expect(["tailored_cv_v1", "cover_letter_v1"]).toContain(item.prompt_version);
    expect(item.validation_status).toBe("passed");
  }

  // --- Another user can't see any of it ---------------------------------------------------------------------------
  const otherEmail = `e2e-documents-other-${Date.now()}@example.com`;
  const registration = await request.post(`${API}/auth/register`, {
    data: { email: otherEmail, password, first_name: "Max", last_name: "Muster" },
  });
  expect(registration.status()).toBe(201);
  const otherHeaders = { Authorization: `Bearer ${await apiToken(request, otherEmail)}` };
  const documentId = (list.items as { id: string }[])[0]!.id;
  expect((await request.get(`${API}/applications/${applicationId}/documents`, { headers: otherHeaders })).status()).toBe(404);
  expect((await request.get(`${API}/applications/${applicationId}/documents/${documentId}`, { headers: otherHeaders })).status()).toBe(404);
  expect((await request.post(`${API}/applications/${applicationId}/documents/cv`, { headers: otherHeaders })).status()).toBe(404);

  // ... and a signed-out visitor sees none of it either.
  const stranger = await browser.newContext();
  const anonymous = await stranger.newPage();
  await anonymous.goto(`/applications/${applicationId}`);
  await expect(anonymous.getByText("Alex Example")).toHaveCount(0);
  await stranger.close();
});
