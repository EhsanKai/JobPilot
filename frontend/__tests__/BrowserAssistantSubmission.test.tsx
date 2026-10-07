import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserAssistant } from "@/components/applications/BrowserAssistant";

import {
  makeFieldList,
  makeSession,
  makeSessionList,
  stoppedSession,
  userControlSession,
} from "./fixtures/automation";
import { makeAttempt } from "./fixtures/submission";

const automation = {
  listSessions: vi.fn(),
  listFields: vi.fn(),
  listEvents: vi.fn(),
  getSession: vi.fn(),
};
const submission = { listSubmissions: vi.fn(), getFinalReview: vi.fn() };

vi.mock("@/lib/api/automation", () => ({
  listSessions: (...a: unknown[]) => automation.listSessions(...a),
  listFields: (...a: unknown[]) => automation.listFields(...a),
  listEvents: (...a: unknown[]) => automation.listEvents(...a),
  getSession: (...a: unknown[]) => automation.getSession(...a),
  startSession: vi.fn(),
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
}));

vi.mock("@/lib/api/submission", () => ({
  listSubmissions: (...a: unknown[]) => submission.listSubmissions(...a),
  getFinalReview: (...a: unknown[]) => submission.getFinalReview(...a),
  confirmFinalReview: vi.fn(),
  authorizeConsent: vi.fn(),
  revokeConsent: vi.fn(),
  authorizeSubmission: vi.fn(),
  submitApplication: vi.fn(),
  cancelAuthorization: vi.fn(),
  verifySubmission: vi.fn(),
}));

async function renderWith(session: ReturnType<typeof makeSession>, items = [] as ReturnType<typeof makeAttempt>[]) {
  automation.listSessions.mockResolvedValue(makeSessionList([session]));
  automation.listFields.mockResolvedValue(makeFieldList([]));
  automation.listEvents.mockResolvedValue({ items: [] });
  submission.listSubmissions.mockResolvedValue({
    items,
    submitted: items.some((a) => a.result === "SUCCESS"),
    needs_verification: items.some((a) => a.result === "SUBMISSION_UNKNOWN"),
  });
  render(<BrowserAssistant applicationId="app-1" />);
  await screen.findByRole("heading", { name: "Browser assistant" });
  await waitFor(() => expect(submission.listSubmissions).toHaveBeenCalledWith("app-1"));
}

beforeEach(() => {
  [...Object.values(automation), ...Object.values(submission)].forEach((m) => m.mockReset());
});

describe("Browser assistant: review & submit (V0.11)", () => {
  it("offers the final review while the assistant works on the page, never a bare submit", async () => {
    await renderWith(makeSession({ status: "COMPLETED" }));
    expect(await screen.findByRole("button", { name: "Open final review" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit|apply|send application/i })).not.toBeInTheDocument();
    expect(submission.getFinalReview).not.toHaveBeenCalled();
  });

  it("does not offer the final review while the user is in control", async () => {
    await renderWith(userControlSession());
    await screen.findByTestId("assistant-status");
    expect(screen.queryByRole("button", { name: "Open final review" })).not.toBeInTheDocument();
  });

  it("shows the latest result, and no final review once the application is submitted", async () => {
    await renderWith(stoppedSession(), [makeAttempt()]);
    const result = await screen.findByTestId("submission-result");
    expect(result).toHaveTextContent("Application submitted successfully.");
    expect(result).toHaveTextContent("ABC-12345");
    expect(screen.queryByRole("button", { name: "Open final review" })).not.toBeInTheDocument();
  });

  it("keeps an unknown result in front of the user", async () => {
    await renderWith(stoppedSession(), [
      makeAttempt({ result: "SUBMISSION_UNKNOWN", confirmation_reference: null }),
    ]);
    const result = await screen.findByTestId("submission-result");
    expect(result).toHaveTextContent("JobPilot will NOT submit again automatically");
  });
});
