import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResumeAnalysis } from "@/components/resume/ResumeAnalysis";
import { ApiError } from "@/lib/http";

import { makeIntelligence, makeProfile, makeResume } from "./fixtures/resumeProfile";

const processResumeMock = vi.fn();
const getResumeIntelligenceMock = vi.fn();
vi.mock("@/lib/api/resumes", () => ({
  processResume: (...args: unknown[]) => processResumeMock(...args),
  getResumeIntelligence: (...args: unknown[]) => getResumeIntelligenceMock(...args),
}));

describe("ResumeAnalysis", () => {
  beforeEach(() => {
    processResumeMock.mockReset();
    getResumeIntelligenceMock.mockReset();
  });

  it("offers an explicit Analyze button for a newly uploaded resume", async () => {
    render(<ResumeAnalysis resume={makeResume()} />);

    expect(await screen.findByRole("button", { name: "Analyze Resume" })).toBeInTheDocument();
    // Nothing happens without the user asking.
    expect(processResumeMock).not.toHaveBeenCalled();
    expect(getResumeIntelligenceMock).not.toHaveBeenCalled();
  });

  it("starts analysis, shows the processing state, then the result", async () => {
    processResumeMock.mockResolvedValue({
      resume: makeResume({ status: "extracting" }),
      profile_id: "profile-1",
      reused: false,
      message: "Resume analysis started.",
    });
    getResumeIntelligenceMock
      .mockResolvedValueOnce(
        makeIntelligence({
          resume: makeResume({ status: "parsing" }),
          is_processing: true,
          profile: null,
        })
      )
      .mockResolvedValue(makeIntelligence());

    const user = userEvent.setup();
    render(<ResumeAnalysis resume={makeResume()} pollIntervalMs={5} />);
    await user.click(await screen.findByRole("button", { name: "Analyze Resume" }));

    expect(processResumeMock).toHaveBeenCalledWith("resume-1", false);
    expect(await screen.findByText("Analyzing your resume...")).toBeInTheDocument();

    expect(await screen.findByText("3 skills detected")).toBeInTheDocument();
    expect(screen.getByText("1 work experience detected")).toBeInTheDocument();
    expect(screen.getByText("1 language detected")).toBeInTheDocument();
    expect(screen.getByText(/review required/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review Resume" })).toHaveAttribute(
      "href",
      "/profile/resume/review"
    );
  });

  it("shows a useful error when the analysis fails", async () => {
    getResumeIntelligenceMock.mockResolvedValue(
      makeIntelligence({
        resume: makeResume({ status: "failed" }),
        profile: null,
        latest_attempt: {
          profile_id: "profile-1",
          status: "failed",
          error_code: "AI_TIMEOUT",
          error_message: "The AI service took too long to respond. Please try again.",
          parser_version: "resume_parser_v1",
          started_at: null,
          processed_at: null,
        },
      })
    );

    render(<ResumeAnalysis resume={makeResume({ status: "failed" })} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The AI service took too long to respond."
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("explains when OCR is required and doesn't offer a pointless retry", async () => {
    getResumeIntelligenceMock.mockResolvedValue(
      makeIntelligence({
        resume: makeResume({ status: "ocr_required" }),
        profile: null,
        latest_attempt: {
          profile_id: "profile-1",
          status: "ocr_required",
          error_code: "OCR_REQUIRED",
          error_message:
            "This PDF does not contain extractable text. OCR support will be added in a future version.",
          parser_version: "resume_parser_v1",
          started_at: null,
          processed_at: null,
        },
      })
    );

    render(<ResumeAnalysis resume={makeResume({ status: "ocr_required" })} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This PDF does not contain extractable text. OCR support will be added in a future version."
    );
    expect(screen.queryByRole("button", { name: /analyze|try again/i })).not.toBeInTheDocument();
  });

  it("shows an error when starting the analysis is rejected", async () => {
    processResumeMock.mockRejectedValue(
      new ApiError(409, "CONFLICT", "This resume is already being analyzed.", {})
    );
    const user = userEvent.setup();
    render(<ResumeAnalysis resume={makeResume()} />);

    await user.click(await screen.findByRole("button", { name: "Analyze Resume" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This resume is already being analyzed."
    );
  });

  it("asks for confirmation before reprocessing, then forces a new analysis", async () => {
    getResumeIntelligenceMock.mockResolvedValue(makeIntelligence());
    processResumeMock.mockResolvedValue({
      resume: makeResume({ status: "extracting" }),
      profile_id: "profile-2",
      reused: false,
      message: "Resume analysis started.",
    });
    const user = userEvent.setup();
    render(<ResumeAnalysis resume={makeResume({ status: "needs_review" })} pollIntervalMs={5} />);

    await user.click(await screen.findByRole("button", { name: "Reprocess Resume" }));
    expect(processResumeMock).not.toHaveBeenCalled();
    expect(screen.getByText(/replaces the current result/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Yes, reprocess" }));
    expect(processResumeMock).toHaveBeenCalledWith("resume-1", true);
  });

  it("tells the user when an existing result was reused", async () => {
    processResumeMock.mockResolvedValue({
      resume: makeResume({ status: "needs_review" }),
      profile_id: "profile-1",
      reused: true,
      message: "This resume was already analyzed. Showing the existing result.",
    });
    getResumeIntelligenceMock.mockResolvedValue(makeIntelligence());
    const user = userEvent.setup();
    render(<ResumeAnalysis resume={makeResume()} />);

    await user.click(await screen.findByRole("button", { name: "Analyze Resume" }));

    expect(await screen.findByText(/already analyzed/i)).toBeInTheDocument();
    expect(await screen.findByText("3 skills detected")).toBeInTheDocument();
  });

  it("links to the confirmed profile and keeps a previous result after a failed re-run", async () => {
    getResumeIntelligenceMock.mockResolvedValue(
      makeIntelligence({
        resume: makeResume({ status: "confirmed" }),
        profile: makeProfile({ status: "confirmed" }),
        latest_attempt: {
          profile_id: "profile-2",
          status: "failed",
          error_code: "AI_RATE_LIMITED",
          error_message: "The AI service is receiving too many requests.",
          parser_version: "resume_parser_v1",
          started_at: null,
          processed_at: null,
        },
      })
    );

    render(<ResumeAnalysis resume={makeResume({ status: "confirmed" })} />);

    expect(await screen.findByRole("link", { name: "View Resume Profile" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Re-analysis failed.*previous analysis is still available/
      )
    );
  });
});
