import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserAssistant } from "@/components/applications/BrowserAssistant";
import { ApiError } from "@/lib/http";
import type { AutomationSession, FormField, QuestionBrief } from "@/types/automation";

import {
  blockedSession,
  failedSession,
  makeChoice,
  makeCounts,
  makeField,
  makeFieldList,
  makeFileDocument,
  makeQuestion,
  makeSession,
  makeSessionList,
  pausedSession,
  stoppedSession,
  userControlSession,
} from "./fixtures/automation";

const api = {
  listSessions: vi.fn(),
  startSession: vi.fn(),
  getSession: vi.fn(),
  listFields: vi.fn(),
  listEvents: vi.fn(),
  pauseSession: vi.fn(),
  takeControl: vi.fn(),
  resumeSession: vi.fn(),
  stopSession: vi.fn(),
  confirmDomain: vi.fn(),
  discoverFields: vi.fn(),
  fillFields: vi.fn(),
  suggestAnswers: vi.fn(),
  setMapping: vi.fn(),
  listDocumentChoices: vi.fn(),
  selectDocument: vi.fn(),
  clearDocument: vi.fn(),
  fetchScreenshot: vi.fn(),
};

vi.mock("@/lib/api/automation", () => ({
  listSessions: (...a: unknown[]) => api.listSessions(...a),
  startSession: (...a: unknown[]) => api.startSession(...a),
  getSession: (...a: unknown[]) => api.getSession(...a),
  listFields: (...a: unknown[]) => api.listFields(...a),
  listEvents: (...a: unknown[]) => api.listEvents(...a),
  pauseSession: (...a: unknown[]) => api.pauseSession(...a),
  takeControl: (...a: unknown[]) => api.takeControl(...a),
  resumeSession: (...a: unknown[]) => api.resumeSession(...a),
  stopSession: (...a: unknown[]) => api.stopSession(...a),
  confirmDomain: (...a: unknown[]) => api.confirmDomain(...a),
  discoverFields: (...a: unknown[]) => api.discoverFields(...a),
  fillFields: (...a: unknown[]) => api.fillFields(...a),
  suggestAnswers: (...a: unknown[]) => api.suggestAnswers(...a),
  setMapping: (...a: unknown[]) => api.setMapping(...a),
  listDocumentChoices: (...a: unknown[]) => api.listDocumentChoices(...a),
  selectDocument: (...a: unknown[]) => api.selectDocument(...a),
  clearDocument: (...a: unknown[]) => api.clearDocument(...a),
  fetchScreenshot: (...a: unknown[]) => api.fetchScreenshot(...a),
}));

async function renderAssistant(
  sessions: AutomationSession[] = [],
  fields: FormField[] = [],
  questions: QuestionBrief[] = [],
  listOverrides = {}
) {
  api.listSessions.mockResolvedValue(makeSessionList(sessions, listOverrides));
  api.listFields.mockResolvedValue(makeFieldList(fields, questions));
  api.listEvents.mockResolvedValue({ items: [] });
  render(<BrowserAssistant applicationId="app-1" />);
  await screen.findByRole("heading", { name: "Browser assistant" });
  await waitFor(() => expect(screen.queryByText("Loading browser assistant...")).not.toBeInTheDocument());
}

function row(label: string) {
  return within(screen.getByRole("row", { name: `Field ${label}` }));
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
});

afterEach(() => {
  vi.useRealTimers();
});

describe("starting the assistant", () => {
  it("states the principle and does nothing until the user starts it", async () => {
    await renderAssistant();
    expect(screen.getByText(/JobPilot fills forms; you remain in control/)).toBeInTheDocument();
    expect(screen.getByText(/never submits your application/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start browser assistant" })).toBeEnabled();
    expect(api.startSession).not.toHaveBeenCalled();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("starts the browser assistant and shows what it found", async () => {
    const user = userEvent.setup();
    await renderAssistant();
    const started = makeSession({ counts: makeCounts({ found: 2, needs_input: 1, ready: 1 }) });
    api.startSession.mockResolvedValue(started);
    api.listFields.mockResolvedValue(
      makeFieldList([makeField({ label: "First name" }), makeField({ label: "Phone number" })])
    );

    await user.click(screen.getByRole("button", { name: "Start browser assistant" }));

    expect(api.startSession).toHaveBeenCalledWith("app-1");
    expect(await screen.findByTestId("assistant-status")).toHaveTextContent("Running");
    expect(await screen.findByText(/Found 2 fields/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Form fields" })).toBeInTheDocument();
    expect(row("First name").getByText("Ready")).toBeInTheDocument();
  });

  it("cannot be started when the application has no application page, and says why", async () => {
    await renderAssistant([], [], [], {
      can_start: false,
      cannot_start_reason: "NO_APPLICATION_URL",
      cannot_start_message: "This application has no application URL.",
    });
    expect(screen.getByRole("button", { name: "Start browser assistant" })).toBeDisabled();
    expect(screen.getByText("This application has no application URL.")).toBeInTheDocument();
  });

  it("shows an error when the session cannot be started", async () => {
    const user = userEvent.setup();
    await renderAssistant();
    api.startSession.mockRejectedValue(
      new ApiError(503, "TOO_MANY_BROWSERS", "The server is running as many browsers as it allows.", {})
    );
    await user.click(screen.getByRole("button", { name: "Start browser assistant" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("as many browsers as it allows");
  });

  it("offers no way to submit the application", async () => {
    await renderAssistant([makeSession({ submit_detected: true })], [makeField()]);
    for (const name of [/submit/i, /apply/i, /send application/i, /finish/i]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name })).not.toBeInTheDocument();
    }
  });
});

describe("the browser state", () => {
  it("shows status, current page and the counts", async () => {
    await renderAssistant([makeSession()], [makeField()]);
    expect(screen.getByTestId("assistant-status")).toHaveTextContent("Running");
    expect(screen.getByText("Application form")).toBeInTheDocument();
    for (const [label, value] of [
      ["Fields found", "14"],
      ["Filled", "9"],
      ["Needs review", "3"],
      ["Needs input", "2"],
    ]) {
      const term = screen.getByText(`${label}:`, { exact: false });
      expect(term.parentElement).toHaveTextContent(`${label}: ${value}`);
    }
  });

  it("is honest about a browser without a window", async () => {
    await renderAssistant([makeSession({ browser_mode: "headless" })], [makeField()]);
    const view = screen.getByText("Live view").closest("details")!;
    expect(within(view).getByText(/without a window/)).toBeInTheDocument();
    expect(within(view).getByRole("link", { name: "Open application website" })).toHaveAttribute(
      "href",
      "https://jobs.example.com/apply"
    );
  });

  it("describes a visible window", async () => {
    await renderAssistant([makeSession({ browser_mode: "visible" })], [makeField()]);
    const view = screen.getByText("Live view").closest("details")!;
    expect(within(view).getByText(/browser window on the computer that runs JobPilot/)).toBeInTheDocument();
  });

  it("loads a live view picture on request", async () => {
    const user = userEvent.setup();
    api.fetchScreenshot.mockResolvedValue("blob:picture");
    await renderAssistant([makeSession({ browser_mode: "headless" })], [makeField()]);
    await user.click(screen.getByText("Live view"));
    await user.click(screen.getByRole("button", { name: "Show view" }));
    expect(await screen.findByAltText("What the browser shows now")).toHaveAttribute("src", "blob:picture");
  });

  it("polls while the assistant is live", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const session = makeSession();
    await renderAssistant([session], [makeField()]);
    api.getSession.mockResolvedValue(
      makeSession({ id: session.id, counts: makeCounts({ found: 14, filled: 12 }) })
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(api.getSession).toHaveBeenCalledWith("app-1", session.id);
    expect(screen.getByText("Filled:", { exact: false }).parentElement).toHaveTextContent("Filled: 12");
  });
});

describe("human control", () => {
  it("pauses, takes control, resumes and stops", async () => {
    const user = userEvent.setup();
    const running = makeSession();
    await renderAssistant([running], [makeField()]);

    api.pauseSession.mockResolvedValue(pausedSession({ id: running.id }));
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(api.pauseSession).toHaveBeenCalledWith("app-1", running.id);
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume assistant" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Fill approved answers" })).not.toBeInTheDocument();

    api.resumeSession.mockResolvedValue(makeSession({ id: running.id }));
    await user.click(screen.getByRole("button", { name: "Resume assistant" }));
    expect(api.resumeSession).toHaveBeenCalledWith("app-1", running.id, true);
    await screen.findByRole("button", { name: "Pause" });

    api.takeControl.mockResolvedValue(userControlSession({ id: running.id }));
    await user.click(screen.getByRole("button", { name: "Take control" }));
    expect(api.takeControl).toHaveBeenCalledWith("app-1", running.id);
    expect(await screen.findByText("User control")).toBeInTheDocument();
    expect(screen.getByText(/You are in control of the browser/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fill approved answers" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rediscover fields" })).not.toBeInTheDocument();

    api.resumeSession.mockResolvedValue(makeSession({ id: running.id }));
    await user.click(screen.getByRole("button", { name: "Resume assistant" }));
    await screen.findByRole("button", { name: "Take control" });

    const stopped = stoppedSession({ id: running.id });
    api.stopSession.mockResolvedValue(stopped);
    api.listSessions.mockResolvedValue(makeSessionList([stopped]));
    await user.click(screen.getByRole("button", { name: "Stop assistant" }));
    expect(api.stopSession).toHaveBeenCalledWith("app-1", running.id, false);
    expect(await screen.findByText(/no longer touches the browser/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start browser assistant" })).toBeEnabled();
  });

  it("can stop and close the browser", async () => {
    const user = userEvent.setup();
    const running = makeSession();
    await renderAssistant([running], [makeField()]);
    api.stopSession.mockResolvedValue(stoppedSession({ id: running.id }));
    await user.click(screen.getByRole("button", { name: "Stop and close the browser" }));
    expect(api.stopSession).toHaveBeenCalledWith("app-1", running.id, true);
  });

  it("fills approved answers and rediscovers fields only when asked", async () => {
    const user = userEvent.setup();
    const running = makeSession();
    await renderAssistant([running], [makeField()]);
    expect(api.fillFields).not.toHaveBeenCalled();
    api.fillFields.mockResolvedValue(makeSession({ id: running.id, counts: makeCounts({ found: 14, filled: 14 }) }));
    await user.click(screen.getByRole("button", { name: "Fill approved answers" }));
    expect(api.fillFields).toHaveBeenCalledWith("app-1", running.id);
    api.discoverFields.mockResolvedValue(running);
    await user.click(screen.getByRole("button", { name: "Rediscover fields" }));
    expect(api.discoverFields).toHaveBeenCalledWith("app-1", running.id);
    expect(screen.getByText(/Only approved answers are filled/)).toBeInTheDocument();
  });

  it("reports suggestions without accepting or filling anything", async () => {
    const user = userEvent.setup();
    const running = makeSession();
    await renderAssistant([running], [makeField()]);
    api.suggestAnswers.mockResolvedValue({ requested: 3, suggested: 2, failed: 1, session: running });
    await user.click(screen.getByRole("button", { name: "Suggest answers" }));
    expect(await screen.findByText(/Suggestions are ready for 2 of 3 questions/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is filled until you accept it/)).toBeInTheDocument();
    expect(api.fillFields).not.toHaveBeenCalled();
  });

  it("asks the user to confirm an unexpected website before continuing", async () => {
    const user = userEvent.setup();
    const paused = pausedSession({
      attention: { code: "UNEXPECTED_DOMAIN", message: "The browser moved to a different website." },
      current_url: "https://other.example.org/page",
      controls: { ...pausedSession().controls, can_resume: false, can_confirm_domain: true },
    });
    await renderAssistant([paused], [makeField()]);
    expect(screen.getByText("The browser moved to a different website.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume assistant" })).not.toBeInTheDocument();
    api.confirmDomain.mockResolvedValue(makeSession({ id: paused.id }));
    await user.click(screen.getByRole("button", { name: "This website is part of my application" }));
    expect(api.confirmDomain).toHaveBeenCalledWith("app-1", paused.id, "other.example.org");
  });
});

describe("blocked and failed", () => {
  it("hands a security check back to the user", async () => {
    const user = userEvent.setup();
    const blocked = blockedSession();
    await renderAssistant([blocked]);
    expect(screen.getByTestId("assistant-status")).toHaveTextContent("Blocked");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("does not solve or work around");
    expect(screen.getByRole("button", { name: "Take control" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Fill approved answers" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume assistant" })).not.toBeInTheDocument();
    api.takeControl.mockResolvedValue(
      userControlSession({ id: blocked.id, attention: blocked.attention })
    );
    await user.click(screen.getByRole("button", { name: "Take control" }));
    expect(await screen.findByRole("button", { name: "Resume assistant" })).toBeEnabled();
  });

  it("asks for a manual login", async () => {
    await renderAssistant([
      blockedSession({
        attention: {
          code: "LOGIN_REQUIRED",
          message: "Login required. Please take control of the browser and sign in manually.",
        },
      }),
    ]);
    expect(screen.getByRole("alert")).toHaveTextContent("Login required. Please take control");
  });

  it("shows a browser error and allows a new start", async () => {
    await renderAssistant([failedSession()]);
    expect(screen.getByTestId("assistant-status")).toHaveTextContent("Failed");
    expect(screen.getByRole("alert")).toHaveTextContent("The page could not be loaded.");
    expect(screen.getByRole("button", { name: "Start browser assistant" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
  });

  it("tells the user that the final submission is theirs", async () => {
    await renderAssistant([
      makeSession({
        submit_detected: true,
        submit_notice:
          "The application appears ready for submission. V0.10 does not submit applications. Please submit manually.",
      }),
    ]);
    expect(screen.getByText(/does not submit applications. Please submit manually/)).toBeInTheDocument();
  });
});

describe("the discovered fields", () => {
  it("shows required fields without an approved answer as needing input", async () => {
    await renderAssistant(
      [makeSession()],
      [
        makeField({
          label: "Email address",
          required: true,
          required_missing: true,
          display_status: "needs_input",
          display_message: "Required — no approved answer.",
          answer: { state: "NEEDS_INPUT", source: null, text: null, options: null, reason: "NO_ANSWER", message: "No approved answer yet." },
        }),
      ]
    );
    const email = row("Email address");
    expect(email.getByText("Needs your input")).toBeInTheDocument();
    expect(email.getByText("Required — no approved answer.")).toBeInTheDocument();
    expect(email.getByText("Required")).toBeInTheDocument();
  });

  it("shows what will be filled and where it comes from", async () => {
    await renderAssistant([makeSession()], [makeField({ label: "Salary" })]);
    const salary = row("Salary");
    expect(salary.getByText(/Will fill \(from your application data\)/)).toBeInTheDocument();
    expect(salary.getByText("jane@example.com")).toBeInTheDocument();
  });

  it("shows an AI suggestion for review, not as something that will be filled", async () => {
    await renderAssistant(
      [makeSession()],
      [
        makeField({
          label: "Why us?",
          display_status: "needs_review",
          display_message: "A suggestion exists. Accept or edit it before it is filled.",
          answer: { state: "SUGGESTION_AVAILABLE", source: null, text: "Because of the team.", options: null, reason: "SUGGESTION_NOT_REVIEWED", message: null },
        }),
      ]
    );
    const why = row("Why us?");
    expect(why.getByText("Needs your review")).toBeInTheDocument();
    expect(why.getByText(/Suggestion \(not filled until you accept it\)/)).toBeInTheDocument();
    expect(why.queryByText(/Will fill/)).not.toBeInTheDocument();
  });

  it("lets the user confirm, change or leave unmapped an uncertain mapping", async () => {
    const user = userEvent.setup();
    const proposed = makeQuestion({ question_text: "Salary expectation" });
    const other = makeQuestion({ question_text: "Notice period" });
    const uncertain = makeField({
      label: "Expected annual compensation",
      input_type: "text",
      display_status: "needs_review",
      display_message: "JobPilot is not sure which question this is. Please check.",
      mapping: { status: "NEEDS_REVIEW", method: "AI", confidence: "MEDIUM", reason: "AI_NOT_CERTAIN" },
      question: null,
      proposed_question: proposed,
      answer: null,
    });
    await renderAssistant([makeSession()], [uncertain], [proposed, other]);
    api.setMapping.mockResolvedValue(uncertain);
    api.getSession.mockResolvedValue(makeSession());
    const target = row("Expected annual compensation");

    expect(target.getByText("Proposed: Salary expectation")).toBeInTheDocument();
    expect(target.getByText("Needs your review")).toBeInTheDocument();

    await user.click(target.getByRole("button", { name: "Confirm the question for Expected annual compensation" }));
    expect(api.setMapping).toHaveBeenLastCalledWith("app-1", expect.any(String), uncertain.id, { action: "confirm" });

    await user.click(target.getByRole("button", { name: "Change the question for Expected annual compensation" }));
    await user.selectOptions(screen.getByLabelText("Question for Expected annual compensation"), other.id);
    await user.click(screen.getByRole("button", { name: "Use this question" }));
    expect(api.setMapping).toHaveBeenLastCalledWith("app-1", expect.any(String), uncertain.id, {
      action: "change",
      question_id: other.id,
    });

    await user.click(target.getByRole("button", { name: "Leave Expected annual compensation unmapped" }));
    expect(api.setMapping).toHaveBeenLastCalledWith("app-1", expect.any(String), uncertain.id, { action: "unmap" });
  });

  it("does not force the user to map every field", async () => {
    const user = userEvent.setup();
    const unmapped = makeField({
      label: "Hobbies",
      input_type: "text",
      display_status: "unmapped",
      display_message: "Not matched to a question, so it is never filled.",
      mapping: { status: "UNMAPPED", method: "USER", confidence: null, reason: "LEFT_UNMAPPED" },
      question: null,
      answer: null,
    });
    await renderAssistant([makeSession()], [unmapped]);
    const hobbies = row("Hobbies");
    expect(hobbies.getByText("Not matched", { selector: "span.inline-block" })).toBeInTheDocument();
    api.setMapping.mockResolvedValue(unmapped);
    api.getSession.mockResolvedValue(makeSession());
    await user.click(hobbies.getByRole("button", { name: "Add a question for Hobbies" }));
    expect(api.setMapping).toHaveBeenCalledWith("app-1", expect.any(String), unmapped.id, { action: "create" });
  });

  it("never offers to tick a legal confirmation", async () => {
    await renderAssistant(
      [makeSession()],
      [
        makeField({
          label: "I agree to the privacy policy",
          input_type: "checkbox",
          required: true,
          display_status: "action_required",
          display_message: "A legal confirmation or consent. Only you can give it: JobPilot never ticks these.",
          user_action_reason: "LEGAL_CONSENT",
          user_action_message: "A legal confirmation or consent. Only you can give it: JobPilot never ticks these.",
          mapping: { status: "UNMAPPED", method: null, confidence: null, reason: null },
          question: null,
          answer: null,
        }),
      ]
    );
    const consent = row("I agree to the privacy policy");
    expect(consent.getByText("Needs you")).toBeInTheDocument();
    expect(consent.getByText(/Only you can give it: JobPilot never ticks these/, { selector: "p" })).toBeInTheDocument();
    expect(consent.queryByRole("button")).not.toBeInTheDocument();
    expect(consent.getByText("Not a question")).toBeInTheDocument();
  });

  it("keeps what the user changed and replaces it only after an explicit confirmation", async () => {
    const user = userEvent.setup();
    const running = makeSession();
    const modified = makeField({
      label: "Phone number",
      display_status: "user_modified",
      display_message: "You changed this field. JobPilot leaves your edit alone.",
      fill: { status: "USER_MODIFIED", error_code: null, error_message: null, last_filled_at: null },
    });
    await renderAssistant([running], [modified]);
    const phone = row("Phone number");
    expect(phone.getByText("You changed this")).toBeInTheDocument();
    expect(phone.getByText("You changed this field. JobPilot leaves your edit alone.")).toBeInTheDocument();

    await user.click(phone.getByRole("button", { name: "Fill Phone number again" }));
    expect(api.fillFields).not.toHaveBeenCalled();
    expect(phone.getByText("Replace what you typed with the approved answer?")).toBeInTheDocument();
    await user.click(phone.getByRole("button", { name: "Keep mine" }));
    expect(api.fillFields).not.toHaveBeenCalled();

    api.fillFields.mockResolvedValue(running);
    await user.click(phone.getByRole("button", { name: "Fill Phone number again" }));
    await user.click(phone.getByRole("button", { name: "Replace" }));
    expect(api.fillFields).toHaveBeenCalledWith("app-1", running.id, {
      field_ids: [modified.id],
      overwrite_field_ids: [modified.id],
    });
  });

  it("shows a failed fill with its reason", async () => {
    await renderAssistant(
      [makeSession()],
      [
        makeField({
          label: "Reference code",
          display_status: "failed",
          display_message: "The browser shows a different value than the approved answer.",
          fill: { status: "FILL_FAILED", error_code: "VALUE_MISMATCH", error_message: "x", last_filled_at: null },
        }),
      ]
    );
    const failed = row("Reference code");
    expect(failed.getByText("Fill failed")).toBeInTheDocument();
    expect(failed.getByText(/shows a different value/)).toBeInTheDocument();
  });

  it("dims a field that is no longer on the page", async () => {
    await renderAssistant([makeSession()], [makeField({ label: "Visa details", present: false })]);
    expect(row("Visa details").getByText(/not on the page now/)).toBeInTheDocument();
  });
});

describe("file fields and document selection", () => {
  const fileField = (overrides: Partial<FormField> = {}) =>
    makeField({
      label: "Resume / CV",
      input_type: "file",
      required: true,
      display_status: "needs_review",
      display_message: "Check which file will be attached: use this one or choose another.",
      question: null,
      answer: null,
      document: makeFileDocument(),
      ...overrides,
    });

  it("shows what the field appears to request and which file it would use", async () => {
    await renderAssistant([makeSession()], [fileField()]);
    const cv = row("Resume / CV");
    expect(cv.getByText(/This field appears to request:/)).toHaveTextContent("Resume / CV");
    expect(cv.getByText("Tailored CV v3")).toBeInTheDocument();
    expect(cv.getByRole("button", { name: "Use this file for Resume / CV" })).toBeEnabled();
    expect(cv.getByRole("button", { name: "Choose another file for Resume / CV" })).toBeEnabled();
    expect(api.selectDocument).not.toHaveBeenCalled(); // nothing is attached before "Use this file"
  });

  it("uses the proposed file only when the user presses Use this file", async () => {
    const user = userEvent.setup();
    const field = fileField();
    await renderAssistant([makeSession()], [field]);
    api.selectDocument.mockResolvedValue(field);
    await user.click(row("Resume / CV").getByRole("button", { name: "Use this file for Resume / CV" }));
    expect(api.selectDocument).toHaveBeenCalledWith(
      "app-1",
      expect.any(String),
      field.id,
      "tailored_cv",
      field.document!.proposed!.id
    );
  });

  it("lets the user choose another document", async () => {
    const user = userEvent.setup();
    const older = makeChoice({ label: "Tailored CV v2", is_current: false, version_number: 2 });
    const letter = makeChoice({ type: "cover_letter", label: "Cover letter v1", version_number: 1 });
    const field = fileField();
    await renderAssistant([makeSession()], [field]);
    api.listDocumentChoices.mockResolvedValue({ items: [field.document!.proposed!, older, letter] });
    api.selectDocument.mockResolvedValue(field);
    const cv = row("Resume / CV");
    await user.click(cv.getByRole("button", { name: "Choose another file for Resume / CV" }));
    const select = await screen.findByLabelText("Document for Resume / CV");
    await user.selectOptions(select, older.id);
    await user.click(screen.getByRole("button", { name: "Use the selected file for Resume / CV" }));
    expect(api.selectDocument).toHaveBeenCalledWith("app-1", expect.any(String), field.id, "tailored_cv", older.id);
  });

  it("keeps the chosen version and says when a newer one exists", async () => {
    const chosen = makeChoice({ label: "Tailored CV v3", is_current: false });
    await renderAssistant(
      [makeSession()],
      [
        fileField({
          display_status: "ready",
          document: makeFileDocument({
            selected: chosen,
            proposed: makeChoice({ label: "Tailored CV v4", version_number: 4 }),
            confirmed: true,
            newer_available: true,
          }),
        }),
      ]
    );
    const cv = row("Resume / CV");
    expect(cv.getByText("Tailored CV v3")).toBeInTheDocument();
    expect(cv.getByText(/A newer version exists, but your choice stays Tailored CV v3/)).toBeInTheDocument();
    expect(cv.queryByRole("button", { name: /Use this file for/ })).not.toBeInTheDocument();
    expect(cv.getByRole("button", { name: "Remove the chosen file for Resume / CV" })).toBeEnabled();
  });

  it("does not guess an unclear field", async () => {
    await renderAssistant(
      [makeSession()],
      [
        fileField({
          label: "Other documents",
          display_status: "action_required",
          document: makeFileDocument({ detected_type: null, proposed: null }),
        }),
      ]
    );
    const other = row("Other documents");
    expect(other.getByText(/It is not clear which document this field asks for/, { selector: "p.text-slate-700" })).toBeInTheDocument();
    expect(other.queryByRole("button", { name: /Use this file for/ })).not.toBeInTheDocument();
    expect(other.getByRole("button", { name: "Choose another file for Other documents" })).toBeEnabled();
  });

  it("warns when the form may not take the format", async () => {
    await renderAssistant(
      [makeSession()],
      [fileField({ document: makeFileDocument({ format_accepted: false }) })]
    );
    expect(row("Resume / CV").getByRole("alert")).toHaveTextContent("may not accept this kind of file");
  });
});

describe("activity", () => {
  it("lists what happened without any form values", async () => {
    const user = userEvent.setup();
    await renderAssistant([makeSession()], [makeField()]);
    api.listEvents.mockResolvedValue({
      items: [
        { id: "e1", event_type: "FIELD_FILLED", field_id: "f", data: { source: "USER" }, created_at: "2026-10-06T10:00:00Z" },
        { id: "e2", event_type: "USER_TAKEOVER", field_id: null, data: {}, created_at: "2026-10-06T10:01:00Z" },
      ],
    });
    await user.click(screen.getByText("Activity"));
    expect(await screen.findByText("field filled")).toBeInTheDocument();
    expect(screen.getByText("user takeover")).toBeInTheDocument();
  });
});
