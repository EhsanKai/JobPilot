import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/**
 * V0.9 in a real browser, against the real backend and database: Application
 * Question AI with Human Control at every stage, and Humanize.
 *
 * A question is added, JobPilot suggests an answer, the user edits it by hand,
 * regenerates -- and their answer is NOT overwritten -- and copies it. Structured
 * questions (salary) are answered from the application's data without AI, and a
 * question the resume cannot answer says "needs your input" instead of guessing.
 * Then the Anschreiben is humanized into a new version, the original stays, the
 * user edits and keeps their own words, and a photo is added and removed.
 *
 * The AI is the deterministic stub of backend/e2e/serve.py (it restates the facts it
 * is shown); everything around it -- context, structured output, truth validation,
 * versioning, ownership, the UI -- is the real thing. Named to run after
 * v08-tailored-documents.e2e.ts.
 */

const API = "http://127.0.0.1:8000/api/v1";
const email = `e2e-questions-${Date.now()}@example.com`;
const password = "supersecret123";
const RESUME_PDF = path.join(__dirname, "fixtures", "example-resume.pdf");
// A 1x1 PNG.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64"
);

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

function question(page: Page, text: string): Locator {
  return page.getByRole("article", { name: text });
}

function yourAnswer(page: Page, text: string): Locator {
  return question(page, text).getByRole("region", { name: "Your answer" });
}

test("answer application questions with AI help and human control, then humanize the Anschreiben", async ({
  page,
  request,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await register(page, email);
  const token = await apiToken(request, email);
  const headers = { Authorization: `Bearer ${token}` };
  await confirmResume(request, token);

  // --- Open a job and start the application ---------------------------------------------------------
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Discover jobs" }).click();
  await expect(page.getByText(/Discovery finished/)).toBeVisible({ timeout: 60_000 });
  await page
    .getByRole("article", { name: "Senior SAP ABAP Developer" })
    .filter({ hasText: "Cologne" })
    .getByRole("link", { name: "Details" })
    .click();
  await page.getByRole("button", { name: "Start Application" }).click();
  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
  const applicationId = page.url().split("/").pop()!;

  // The application's own data: language, salary and availability.
  const patched = await request.patch(`${API}/applications/${applicationId}`, {
    headers,
    data: {
      application_language: "de",
      salary_expectation: "78000",
      salary_currency: "EUR",
      mention_salary_in_letter: true,
      available_from: "2027-01-01",
    },
  });
  expect(patched.ok()).toBeTruthy();
  await page.reload();

  // The workspace is navigable: Job · Match · Preparation · CV · Anschreiben · Questions · Notes
  const nav = page.getByRole("navigation", { name: "Workspace sections" });
  for (const name of ["Job", "Match", "Preparation", "CV", "Anschreiben", "Questions", "Notes"]) {
    await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
  }
  await expect(page.getByRole("heading", { level: 2, name: "Application questions" })).toBeVisible();
  await expect(page.getByText("No questions yet.")).toBeVisible();

  // --- Add a question ---------------------------------------------------------------------------------------
  const WHY = "Why do you want to work for our company?";
  await page.getByRole("button", { name: "Add question" }).click();
  await page.getByLabel("Question", { exact: true }).fill(WHY);
  await page.getByLabel("Max characters").fill("500");
  await page.getByLabel("Required").check();
  await page.getByRole("button", { name: "Add question" }).click();
  await expect(question(page, WHY)).toBeVisible();
  await expect(question(page, WHY).getByText("Max 500 characters")).toBeVisible();
  await expect(question(page, WHY).getByText("Required")).toBeVisible();

  // --- JobPilot suggests an answer; it is not the user's answer ---------------------------------------
  await yourAnswer(page, WHY).getByRole("button", { name: "Generate answer" }).click();
  const suggestion = question(page, WHY).getByRole("region", { name: "AI suggestion" });
  await expect(suggestion.getByTestId("suggestion-text")).toContainText("In meiner Arbeit:");
  await expect(suggestion.getByText(/Checked against your resume/)).toBeVisible();
  await expect(suggestion.getByText(/e2e-stub \(e2e-stub-1\) · application_answer_v1/)).toBeVisible();
  await suggestion.getByText(/Sources \(\d\)/).click();
  await expect(suggestion.getByRole("listitem")).toHaveText([
    "Developed and maintained SAP IS-U applications for meter-to-cash processes",
  ]);
  await expect(yourAnswer(page, WHY).getByText("Not answered yet.")).toBeVisible();
  const original = (await suggestion.getByTestId("suggestion-text").textContent())!;

  // --- The user edits it by hand ---------------------------------------------------------------------------
  await suggestion.getByRole("button", { name: "Edit" }).click();
  const box = yourAnswer(page, WHY).getByLabel("Your answer");
  await expect(box).toHaveValue(original);
  await expect(yourAnswer(page, WHY).getByTestId("character-counter")).toContainText("of 500 characters remaining");
  await box.fill("Ich habe rund vier Jahre Erfahrung mit SAP IS-U und möchte diese einbringen.");
  await yourAnswer(page, WHY).getByRole("button", { name: "Save" }).click();
  await expect(yourAnswer(page, WHY).getByTestId("final-answer")).toHaveText(
    "Ich habe rund vier Jahre Erfahrung mit SAP IS-U und möchte diese einbringen."
  );
  await expect(question(page, WHY).getByTestId("answer-status")).toHaveText("Edited by you");
  // the AI's original is still there, apart from the user's answer
  await expect(suggestion.getByTestId("suggestion-text")).toHaveText(original);

  // --- Regenerate: the user is asked, and their answer is NOT overwritten --------------------------------
  await suggestion.getByRole("button", { name: "Regenerate" }).click();
  await expect(question(page, WHY).getByRole("group", { name: "Confirm" })).toContainText(
    "Your current answer has been manually edited. Generate a new AI suggestion without changing your current answer?"
  );
  await question(page, WHY).getByRole("button", { name: "Continue" }).click();
  await expect(question(page, WHY).getByRole("group", { name: "Confirm" })).toHaveCount(0);
  await expect(yourAnswer(page, WHY).getByTestId("final-answer")).toHaveText(
    "Ich habe rund vier Jahre Erfahrung mit SAP IS-U und möchte diese einbringen."
  );
  await expect(question(page, WHY).getByTestId("answer-status")).toHaveText("Edited by you");

  // --- ... also after a reload; the server agrees -----------------------------------------------------------
  await page.reload();
  await expect(yourAnswer(page, WHY).getByTestId("final-answer")).toHaveText(
    "Ich habe rund vier Jahre Erfahrung mit SAP IS-U und möchte diese einbringen."
  );
  const stored = await (await request.get(`${API}/applications/${applicationId}/questions`, { headers })).json();
  const answer = stored.items[0].answer;
  expect(answer.status).toBe("USER_EDITED");
  expect(answer.answer_source).toBe("HYBRID");
  expect(answer.final_answer).toBe("Ich habe rund vier Jahre Erfahrung mit SAP IS-U und möchte diese einbringen.");
  const attempts = await (
    await request.get(`${API}/applications/${applicationId}/questions/${stored.items[0].id}/generations`, { headers })
  ).json();
  expect(attempts.items).toHaveLength(2); // the first suggestion is kept
  expect(attempts.items.every((a: { prompt_version: string }) => a.prompt_version === "application_answer_v1")).toBe(true);

  // --- Copy the exact final answer ---------------------------------------------------------------------------------
  await yourAnswer(page, WHY).getByRole("button", { name: "Copy" }).click();
  await expect(yourAnswer(page, WHY).getByText("Copied")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    "Ich habe rund vier Jahre Erfahrung mit SAP IS-U und möchte diese einbringen."
  );

  // --- A structured question is answered from the application's data: no AI ---------------------------
  const SALARY = "What are your salary expectations?";
  await page.getByRole("button", { name: "Add question" }).click();
  await page.getByLabel("Question", { exact: true }).fill(SALARY);
  await page.getByRole("button", { name: "Add question" }).click();
  await yourAnswer(page, SALARY).getByRole("button", { name: "Generate answer" }).click();
  const salary = question(page, SALARY).getByRole("region", { name: "Suggestion from your application data" });
  await expect(salary.getByTestId("suggestion-text")).toHaveText("78.000 EUR brutto pro Jahr");
  await expect(salary.getByText("Built from your application data. No AI was used.")).toBeVisible();
  await salary.getByRole("button", { name: "Accept" }).click();
  await expect(question(page, SALARY).getByTestId("answer-status")).toHaveText("Accepted");
  await expect(yourAnswer(page, SALARY).getByTestId("final-answer")).toHaveText("78.000 EUR brutto pro Jahr");

  // --- What the resume cannot answer is "needs your input", never a guess -----------------------------------------
  const JAVA = "How many years of Java experience do you have?";
  await page.getByRole("button", { name: "Add question" }).click();
  await page.getByLabel("Question", { exact: true }).fill(JAVA);
  await page.getByLabel("Answer type").selectOption("NUMERIC");
  await page.getByRole("button", { name: "Add question" }).click();
  await yourAnswer(page, JAVA).getByRole("button", { name: "Generate answer" }).click();
  await expect(question(page, JAVA).getByTestId("needs-input")).toContainText("won't guess");
  await expect(question(page, JAVA).getByTestId("answer-status")).toHaveText("Needs your input");
  await yourAnswer(page, JAVA).getByRole("button", { name: "Answer manually" }).click();
  await yourAnswer(page, JAVA).getByLabel("Your answer").fill("many");
  await expect(yourAnswer(page, JAVA).getByRole("button", { name: "Save" })).toBeEnabled();
  await yourAnswer(page, JAVA).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("This question takes a number.")).toBeVisible();
  await yourAnswer(page, JAVA).getByLabel("Your answer").fill("6");
  await yourAnswer(page, JAVA).getByRole("button", { name: "Save" }).click();
  await expect(yourAnswer(page, JAVA).getByTestId("final-answer")).toHaveText("6");

  // --- Another user can't see any of it ---------------------------------------------------------------------------
  const otherEmail = `e2e-questions-other-${Date.now()}@example.com`;
  expect(
    (
      await request.post(`${API}/auth/register`, {
        data: { email: otherEmail, password, first_name: "Max", last_name: "Muster" },
      })
    ).status()
  ).toBe(201);
  const otherHeaders = { Authorization: `Bearer ${await apiToken(request, otherEmail)}` };
  expect((await request.get(`${API}/applications/${applicationId}/questions`, { headers: otherHeaders })).status()).toBe(404);
  expect(
    (await request.get(`${API}/applications/${applicationId}/questions/${stored.items[0].id}`, { headers: otherHeaders })).status()
  ).toBe(404);

  // --- Anschreiben: generate, then Humanize into a new version -------------------------------------------------------
  const letter = page.getByRole("heading", { name: "Cover letter (Anschreiben)", exact: true }).locator("xpath=ancestor::section[1]");
  await letter.getByRole("button", { name: "Generate Anschreiben" }).click();
  await expect(letter.locator('[data-status="generated"]')).toContainText("Version 1");
  await letter.getByRole("button", { name: "View Cover letter (Anschreiben)" }).click();
  const v1 = page.getByRole("region", { name: "Cover letter (Anschreiben), version 1" });
  await expect(v1.getByText(/mit großem Interesse bewerbe ich mich auf die Position/)).toBeVisible();

  await letter.getByRole("button", { name: "Humanize cover letter" }).click();
  await expect(page.getByRole("group", { name: "Confirm humanize" })).toContainText(
    "Humanize this cover letter while preserving all factual content?"
  );
  await page.getByRole("group", { name: "Confirm humanize" }).getByRole("button", { name: "Humanize" }).click();

  const v2 = page.getByRole("region", { name: "Cover letter (Anschreiben), version 2" });
  await expect(v2.getByText(/gerne bewerbe ich mich auf die Position Senior SAP ABAP Developer bei Example GmbH/)).toBeVisible();
  await expect(v2).toContainText("Reworded by Humanize");
  await expect(v2).toContainText("Passed — every statement is backed by your resume");
  await expect(v2.getByRole("group", { name: "Salary statement" })).toHaveText(
    "Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr."
  );
  await expect(letter.locator('[data-status="generated"]')).toContainText("Version 2");
  await expect(letter.locator('[data-status="generated"]')).toContainText("reworded by Humanize");

  // ... and the original is still there, exactly as it was
  await letter.getByText(/History \(2 attempts\)/).click();
  await letter.getByRole("button", { name: "View Cover letter (Anschreiben) version 1" }).click();
  await expect(
    page.getByRole("region", { name: "Cover letter (Anschreiben), version 1" }).getByText(/mit großem Interesse bewerbe ich mich auf die Position/)
  ).toBeVisible();

  // --- The user edits the letter: their words, a new version, never overwritten ------------------------------------------
  await letter.getByRole("button", { name: "View Cover letter (Anschreiben)" }).first().click();
  await letter.getByRole("button", { name: "Edit Cover letter (Anschreiben)" }).click();
  const editor = page.getByRole("region", { name: "Edit Cover letter (Anschreiben)" });
  await editor.getByLabel("Subject").fill("Meine eigene Betreffzeile");
  await editor.getByRole("button", { name: "Save as new version" }).click();
  const v3 = page.getByRole("region", { name: "Cover letter (Anschreiben), version 3" });
  await expect(v3).toContainText("Meine eigene Betreffzeile");
  await expect(v3).toContainText("Your own words — not checked by JobPilot");
  await expect(letter.locator('[data-status="generated"]')).toContainText("Edited by you");

  // Regenerating over the user's edit needs an explicit confirmation, and keeps it in the history.
  await letter.getByRole("button", { name: "Regenerate Cover letter (Anschreiben)" }).click();
  await expect(page.getByRole("group", { name: "Confirm regenerate" })).toContainText("contains your own edits");
  await page.getByRole("group", { name: "Confirm regenerate" }).getByRole("button", { name: "Cancel" }).click();
  const list = await (await request.get(`${API}/applications/${applicationId}/documents`, { headers })).json();
  const letters = (list.items as { document_type: string; version_number: number; origin: string; is_current: boolean }[])
    .filter((item) => item.document_type === "cover_letter")
    .map((item) => `${item.version_number}:${item.origin}:${item.is_current}`)
    .sort();
  expect(letters).toEqual(["1:ai:false", "2:humanized:false", "3:user_edited:true"]);

  // --- The user's own photo: added, shown on the CV, removed ----------------------------------------------------------------
  const cv = page.getByRole("heading", { name: "Tailored CV", exact: true }).locator("xpath=ancestor::section[1]");
  await cv.getByRole("button", { name: "Generate CV" }).click();
  await expect(cv.locator('[data-status="generated"]')).toContainText("Version 1");
  await cv.getByTestId("photo-file").setInputFiles({ name: "me.png", mimeType: "image/png", buffer: PNG });
  await expect(cv.getByRole("img", { name: "Your CV photo" })).toBeVisible();
  await cv.getByRole("button", { name: "View Tailored CV" }).click();
  await expect(page.getByRole("region", { name: "Tailored CV, version 1" }).getByRole("img", { name: "Your photo" })).toBeVisible();
  // regenerating the CV leaves the photo alone
  await cv.getByRole("button", { name: "Regenerate Tailored CV" }).click();
  await expect(cv.locator('[data-status="generated"]')).toContainText("Version 2");
  await expect(cv.getByRole("img", { name: "Your CV photo" })).toBeVisible();
  await cv.getByRole("button", { name: "Remove photo" }).click();
  await expect(cv.getByText("No photo")).toBeVisible();
  expect((await request.get(`${API}/applications/${applicationId}/cv-photo`, { headers })).status()).toBe(404);
});
