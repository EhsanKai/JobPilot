import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FinalReview } from "@/components/applications/assistant/FinalReview";
import { SubmissionResult } from "@/components/applications/assistant/SubmissionResult";
import { ApiError } from "@/lib/http";

import { makeSession } from "./fixtures/automation";
import {
  HASH,
  confirmedReview,
  makeAttempt,
  makeAuthorization,
  makeReview,
  makeReviewRecord,
  makeState,
} from "./fixtures/submission";

const api = {
  getFinalReview: vi.fn(),
  confirmFinalReview: vi.fn(),
  authorizeConsent: vi.fn(),
  revokeConsent: vi.fn(),
  authorizeSubmission: vi.fn(),
  submitApplication: vi.fn(),
  verifySubmission: vi.fn(),
};

vi.mock("@/lib/api/submission", () => ({
  getFinalReview: (...a: unknown[]) => api.getFinalReview(...a),
  confirmFinalReview: (...a: unknown[]) => api.confirmFinalReview(...a),
  authorizeConsent: (...a: unknown[]) => api.authorizeConsent(...a),
  revokeConsent: (...a: unknown[]) => api.revokeConsent(...a),
  authorizeSubmission: (...a: unknown[]) => api.authorizeSubmission(...a),
  submitApplication: (...a: unknown[]) => api.submitApplication(...a),
  verifySubmission: (...a: unknown[]) => api.verifySubmission(...a),
  cancelAuthorization: vi.fn(),
  listSubmissions: vi.fn(),
}));

const session = makeSession({ id: "session-1", status: "COMPLETED" });

async function open(onAttempt = vi.fn(), onTakeControl = vi.fn()) {
  const user = userEvent.setup();
  render(
    <FinalReview
      applicationId="app-1"
      session={session}
      onAttempt={onAttempt}
      onTakeControl={onTakeControl}
    />
  );
  await user.click(screen.getByRole("button", { name: "Open final review" }));
  await screen.findByTestId("final-review");
  await screen.findByText("1. Destination");
  return { user, onAttempt, onTakeControl };
}

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
});

describe("FinalReview", () => {
  it("reads nothing and submits nothing until it is opened", () => {
    render(
      <FinalReview applicationId="app-1" session={session} onAttempt={vi.fn()} onTakeControl={vi.fn()} />
    );
    expect(api.getFinalReview).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /submit/i })).not.toBeInTheDocument();
    expect(screen.getByText(/only after your final review and your explicit authorization/)).toBeInTheDocument();
  });

  it("shows exactly what would be submitted, where, and where each value came from", async () => {
    api.getFinalReview.mockResolvedValue(makeReview());
    await open();
    const review = screen.getByTestId("final-review");
    expect(within(review).getByText("Example GmbH")).toBeInTheDocument();
    expect(within(review).getByText("Senior Software Developer")).toBeInTheDocument();
    expect(within(review).getByTestId("destination-host")).toHaveTextContent("jobs.example.com");

    const personal = within(review).getByRole("table", { name: "Personal information" });
    const email = within(personal).getByRole("row", { name: "Review Email address" });
    expect(email).toHaveTextContent("erika@example.com");
    expect(email).toHaveTextContent("Your answer");

    const questions = within(review).getByRole("table", { name: "Application questions" });
    expect(within(questions).getByRole("row", { name: "Review Salary expectation" })).toHaveTextContent(
      "From your application data"
    );
    expect(
      within(questions).getByRole("row", { name: "Review Why are you interested in this position?" })
    ).toHaveTextContent("AI-generated → accepted by you");

    const documents = within(review).getByRole("list", { name: "Documents" });
    expect(documents).toHaveTextContent("Tailored CV v4");
    expect(documents).toHaveTextContent("edited by you");

    const consents = within(review).getByRole("list", { name: "Consents" });
    const first = within(consents).getByRole("listitem", { name: "Consent 1" });
    expect(first).toHaveTextContent("I agree to the processing of my personal data.");
    expect(first).toHaveTextContent("Authorized by you");
    const second = within(consents).getByRole("listitem", { name: "Consent 2" });
    expect(second).toHaveTextContent("Not authorized by you");
    expect(within(review).getByText("None.")).toBeInTheDocument();
  });

  it("lists every blocker and does not let a blocked review be confirmed", async () => {
    api.getFinalReview.mockResolvedValue(
      makeReview({
        can_confirm_review: false,
        current: makeState({
          ready: false,
          blockers: [
            {
              code: "CONSENT_NOT_AUTHORIZED",
              message: "This consent needs your explicit authorization.",
              field_id: "f-privacy",
              label: "I agree",
            },
            { code: "MULTIPLE_SUBMIT_CONTROLS", message: "More than one button.", field_id: null, label: null },
          ],
        }),
      })
    );
    await open();
    const blockers = screen.getByRole("list", { name: "Blockers" });
    expect(within(blockers).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Confirm final review" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Authorize & Submit" })).not.toBeInTheDocument();
  });

  it("confirms the review for the hash the user saw", async () => {
    api.getFinalReview.mockResolvedValueOnce(makeReview()).mockResolvedValue(confirmedReview());
    api.confirmFinalReview.mockResolvedValue(makeReviewRecord());
    const { user } = await open();
    await user.click(screen.getByRole("button", { name: "Confirm final review" }));
    expect(api.confirmFinalReview).toHaveBeenCalledWith("app-1", "session-1", HASH);
    await screen.findByText(/You confirmed this review/);
  });

  it("gives and withdraws each consent separately, for its exact text", async () => {
    api.getFinalReview.mockResolvedValue(makeReview());
    api.authorizeConsent.mockResolvedValue(undefined);
    api.revokeConsent.mockResolvedValue(undefined);
    const { user } = await open();
    await user.click(screen.getByRole("button", { name: "I give consent 2" }));
    expect(api.authorizeConsent).toHaveBeenCalledWith("app-1", "session-1", "f-pool", "d".repeat(64));
    await user.click(screen.getByRole("button", { name: "Withdraw consent 1" }));
    expect(api.revokeConsent).toHaveBeenCalledWith("app-1", "session-1", "f-privacy");
    // There is no "agree to everything".
    expect(screen.queryByRole("button", { name: /all consents|everything/i })).not.toBeInTheDocument();
  });

  it("submits only after the explicit authorization checkbox and the final button", async () => {
    api.getFinalReview.mockResolvedValue(confirmedReview());
    api.authorizeSubmission.mockResolvedValue(makeAuthorization());
    api.submitApplication.mockResolvedValue(makeAttempt());
    const { user, onAttempt } = await open();
    const button = screen.getByRole("button", { name: "Authorize & Submit" });
    expect(button).toBeDisabled();
    await user.click(button);
    expect(api.authorizeSubmission).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole("checkbox", { name: /I have reviewed the information above and authorize JobPilot/ })
    );
    await user.click(screen.getByRole("button", { name: "Authorize & Submit" }));
    await waitFor(() => expect(onAttempt).toHaveBeenCalledWith(makeAttempt()));
    expect(api.authorizeSubmission).toHaveBeenCalledWith("app-1", "session-1", "review-1", HASH);
    expect(api.submitApplication).toHaveBeenCalledWith("app-1", "session-1", "auth-1");
    expect(api.submitApplication).toHaveBeenCalledTimes(1);
  });

  it("reports a refused authorization and submits nothing", async () => {
    api.getFinalReview.mockResolvedValue(confirmedReview());
    api.authorizeSubmission.mockRejectedValue(
      new ApiError(409, "STATE_CHANGED", "The application changed after your final review.", {})
    );
    const { user, onAttempt } = await open();
    await user.click(screen.getByRole("checkbox", { name: /authorize JobPilot to submit/ }));
    await user.click(screen.getByRole("button", { name: "Authorize & Submit" }));
    await screen.findByText("The application changed after your final review.");
    expect(api.submitApplication).not.toHaveBeenCalled();
    expect(onAttempt).not.toHaveBeenCalled();
  });

  it("explains an outdated review and offers to review again", async () => {
    api.getFinalReview.mockResolvedValue(
      makeReview({
        review: makeReviewRecord({
          status: "INVALIDATED",
          invalidation_reason: "USER_TAKEOVER",
          invalidated_at: "2026-10-07T12:40:00Z",
        }),
      })
    );
    await open();
    expect(screen.getByText(/no longer current \(you took control of the browser\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm final review" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Authorize & Submit" })).not.toBeInTheDocument();
  });

  it("lets the user go back and edit anything, or take control", async () => {
    api.getFinalReview.mockResolvedValue(confirmedReview());
    const { user, onTakeControl } = await open();
    const edit = screen.getByRole("group", { name: "Edit before submitting" });
    expect(within(edit).getByRole("link", { name: "Edit answers" })).toHaveAttribute("href", "#questions");
    expect(within(edit).getByRole("link", { name: "Change CV or Anschreiben" })).toHaveAttribute(
      "href",
      "#documents"
    );
    await user.click(within(edit).getByRole("button", { name: "Take control" }));
    expect(onTakeControl).toHaveBeenCalled();
  });
});

describe("SubmissionResult", () => {
  it("says submitted only with the confirmation the employer showed", () => {
    render(<SubmissionResult applicationId="app-1" attempt={makeAttempt()} />);
    const result = screen.getByTestId("submission-result");
    expect(result).toHaveTextContent("Application submitted successfully.");
    expect(result).toHaveTextContent("Example GmbH");
    expect(result).toHaveTextContent("Senior Software Developer");
    expect(result).toHaveTextContent("Application reference ABC-12345");
  });

  it("is honest about an unknown result and never offers a retry", async () => {
    const attempt = makeAttempt({
      result: "SUBMISSION_UNKNOWN",
      message: "unknown",
      confirmation_reference: null,
      failure_code: "NO_CONFIRMATION",
      failure_reason: "The page did not show a confirmation.",
    });
    const onChanged = vi.fn();
    api.verifySubmission.mockResolvedValue({ ...attempt, user_verified_outcome: "NOT_SUBMITTED" });
    render(<SubmissionResult applicationId="app-1" attempt={attempt} onChanged={onChanged} />);
    const result = screen.getByTestId("submission-result");
    expect(result).toHaveTextContent("We could not safely determine whether the application was submitted.");
    expect(result).toHaveTextContent("JobPilot will NOT submit again automatically");
    expect(result).not.toHaveTextContent("submitted successfully");
    expect(screen.queryByRole("button", { name: /submit|retry|try again/i })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Not received" }));
    expect(api.verifySubmission).toHaveBeenCalledWith("app-1", "attempt-1", "NOT_SUBMITTED");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("says nothing was submitted when it failed before the press", () => {
    render(
      <SubmissionResult
        applicationId="app-1"
        attempt={makeAttempt({
          result: "FAILED_BEFORE_SUBMIT",
          submit_triggered_at: null,
          confirmation_reference: null,
          failure_reason: "The application changed after your review.",
        })}
      />
    );
    const result = screen.getByTestId("submission-result");
    expect(result).toHaveTextContent("The application could not be submitted.");
    expect(result).toHaveTextContent("No submission was confirmed");
    expect(result).not.toHaveTextContent("submitted successfully");
  });
});
