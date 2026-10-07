import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MatchBadge } from "@/components/jobs/MatchBadge";
import { MatchPanel } from "@/components/jobs/MatchPanel";
import { ApiError } from "@/lib/http";

import { makeMatchResult, makeMatchState, makeMatchSummary } from "./fixtures/match";

const getMatchMock = vi.fn();
const calculateMatchMock = vi.fn();
const explainMatchMock = vi.fn();

vi.mock("@/lib/api/match", () => ({
  getMatch: (...args: unknown[]) => getMatchMock(...args),
  calculateMatch: (...args: unknown[]) => calculateMatchMock(...args),
  explainMatch: (...args: unknown[]) => explainMatchMock(...args),
}));

describe("MatchPanel", () => {
  beforeEach(() => {
    getMatchMock.mockReset();
    calculateMatchMock.mockReset();
    explainMatchMock.mockReset();
  });

  it("shows 'not calculated' and calculates on request", async () => {
    getMatchMock.mockResolvedValue({ job_id: "job-1", status: "not_calculated", result: null });
    let resolve: (value: unknown) => void = () => {};
    calculateMatchMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<MatchPanel jobId="job-1" />);

    expect(screen.getByText("Loading your match...")).toBeInTheDocument();
    expect(await screen.findByText(/Match not calculated yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Overall match")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Calculate match" }));
    expect(screen.getByText("Calculating your match...")).toBeInTheDocument();
    expect(calculateMatchMock).toHaveBeenCalledWith("job-1");

    resolve(makeMatchState());
    expect(await screen.findByLabelText("Overall match")).toHaveTextContent("87%");
    expect(screen.queryByText("Calculating your match...")).not.toBeInTheDocument();
  });

  it("distinguishes overall, job and preference match", async () => {
    getMatchMock.mockResolvedValue(makeMatchState());
    render(<MatchPanel jobId="job-1" />);
    expect(await screen.findByLabelText("Overall match")).toHaveTextContent("87%");
    expect(screen.getByLabelText("Job match")).toHaveTextContent("94%");
    expect(screen.getByLabelText("Preference match")).toHaveTextContent("71%");
    expect(screen.getByLabelText("Recommendation")).toHaveTextContent("Strong match");
    expect(screen.getByText(/Overall match · Strong/)).toBeInTheDocument();
  });

  it("explains strong matches, gaps and unknowns from structured items", async () => {
    getMatchMock.mockResolvedValue(makeMatchState());
    render(<MatchPanel jobId="job-1" />);
    const strong = await screen.findByRole("region", { name: "Why this matches" });
    expect(within(strong).getByText("Python")).toBeInTheDocument();
    expect(within(strong).getByText("6 years of experience (5+ asked)")).toBeInTheDocument();
    const gaps = screen.getByRole("region", { name: "Potential gaps" });
    expect(within(gaps).getByText("Kubernetes (nice to have)")).toBeInTheDocument();
    const unknown = screen.getByRole("region", { name: "Unknown" });
    expect(within(unknown).getByText("Salary not stated")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Deal breakers" })).not.toBeInTheDocument();
  });

  it("keeps detailed evidence behind an expandable section", async () => {
    getMatchMock.mockResolvedValue(makeMatchState());
    render(<MatchPanel jobId="job-1" />);
    const summary = await screen.findByText("Show detailed evidence");
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    await userEvent.click(summary);
    expect(details).toHaveAttribute("open");
    expect(within(details).getByText("“3+ years Python development”")).toBeInTheDocument();
    expect(within(details).getByText(/matching_v1/)).toBeInTheDocument();
  });

  it("shows deal breakers and a capped score", async () => {
    getMatchMock.mockResolvedValue(
      makeMatchState({
        result: makeMatchResult({
          overall_match: 40,
          overall_match_uncapped: 77,
          job_match: 94,
          preference_match: 38,
          recommendation: "NOT_RECOMMENDED",
          recommendation_label: "Not recommended",
          is_blocked: true,
          deal_breakers: [
            { id: "deal_breaker:relocation_preference", kind: "deal_breaker",
              component: "deal_breakers", group: "preference", status: "hard_block",
              importance: null, text: "Requires relocation, and you don't want to relocate" },
          ],
        }),
      })
    );
    render(<MatchPanel jobId="job-1" />);
    const breakers = await screen.findByRole("region", { name: "Deal breakers" });
    expect(within(breakers).getByText(/Requires relocation/)).toBeInTheDocument();
    expect(screen.getByLabelText("Recommendation")).toHaveTextContent("Not recommended");
    expect(screen.getByText(/Capped at 40% \(calculated 77%\)/)).toBeInTheDocument();
  });

  it("marks a stale result and recalculates it", async () => {
    getMatchMock.mockResolvedValue(
      makeMatchState({
        status: "stale",
        result: makeMatchResult({ status: "stale", stale_reasons: ["profile_changed"] }),
      })
    );
    calculateMatchMock.mockResolvedValue(makeMatchState());
    render(<MatchPanel jobId="job-1" />);
    expect(await screen.findByText(/Out of date: your profile/)).toBeInTheDocument();
    // AI explanations are only offered for an up-to-date result.
    expect(screen.getByRole("button", { name: "Explain in plain words (AI)" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Recalculate" }));
    expect(calculateMatchMock).toHaveBeenCalledWith("job-1");
    expect(await screen.findByLabelText("Overall match")).toBeInTheDocument();
    expect(screen.queryByText(/Out of date/)).not.toBeInTheDocument();
  });

  it("shows an error and can retry", async () => {
    getMatchMock.mockRejectedValueOnce(new ApiError(500, "INTERNAL_ERROR", "Server down", {}));
    getMatchMock.mockResolvedValueOnce(makeMatchState());
    render(<MatchPanel jobId="job-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Server down");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Overall match")).toBeInTheDocument();
  });

  it("does not spin forever when calculation fails", async () => {
    getMatchMock.mockResolvedValue({ job_id: "job-1", status: "not_calculated", result: null });
    calculateMatchMock.mockRejectedValue(new ApiError(404, "NOT_FOUND", "Job not found.", {}));
    render(<MatchPanel jobId="job-1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Calculate match" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Job not found.");
    expect(screen.queryByText("Calculating your match...")).not.toBeInTheDocument();
  });

  it("adds an AI explanation on request without changing the score", async () => {
    getMatchMock.mockResolvedValue(makeMatchState());
    explainMatchMock.mockResolvedValue({
      prompt_version: "matching_explanation_v1",
      ai_provider: "fake",
      ai_model: "fake-model-1",
      summary: "Python and your experience fit well; Kubernetes would be a plus.",
      strengths: [],
      concerns: [],
      created_at: "2026-10-06T12:00:00Z",
    });
    render(<MatchPanel jobId="job-1" />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Explain in plain words (AI)" })
    );
    const section = await screen.findByRole("region", { name: "AI explanation" });
    expect(section).toHaveTextContent("Kubernetes would be a plus");
    expect(section).toHaveTextContent("It doesn't change the score");
    expect(screen.getByLabelText("Overall match")).toHaveTextContent("87%");
  });

  it("reports an unavailable AI explanation gracefully", async () => {
    getMatchMock.mockResolvedValue(makeMatchState());
    explainMatchMock.mockRejectedValue(
      new ApiError(503, "AI_NOT_CONFIGURED", "AI explanations are not available.", {})
    );
    render(<MatchPanel jobId="job-1" />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Explain in plain words (AI)" })
    );
    expect(await screen.findByText("AI explanations are not available.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Why this matches" })).toBeInTheDocument();
  });

  it("shows a stored AI explanation", async () => {
    getMatchMock.mockResolvedValue(
      makeMatchState({
        result: makeMatchResult({
          ai_explanation: {
            prompt_version: "matching_explanation_v1", ai_provider: "anthropic",
            ai_model: "model-x", summary: "Stored explanation.", strengths: [], concerns: [],
            created_at: "2026-10-06T12:00:00Z",
          },
        }),
      })
    );
    render(<MatchPanel jobId="job-1" />);
    expect(await screen.findByText("Stored explanation.")).toBeInTheDocument();
    expect(explainMatchMock).not.toHaveBeenCalled();
  });
});

describe("MatchBadge", () => {
  it("never implies a score that wasn't calculated", () => {
    render(<MatchBadge match={null} />);
    expect(screen.getByText("Match not calculated")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("separates overall from job and preference match", () => {
    render(<MatchBadge match={makeMatchSummary()} />);
    expect(screen.getByText("87% Overall match")).toBeInTheDocument();
    expect(screen.getByText("Strong match")).toBeInTheDocument();
    expect(screen.getByText("Job match 94% · Preference match 71%")).toBeInTheDocument();
  });

  it("flags outdated results", () => {
    render(<MatchBadge match={makeMatchSummary({ status: "stale" })} />);
    expect(screen.getByText("Outdated")).toBeInTheDocument();
  });

  it("handles insufficient data without an overall score", () => {
    render(
      <MatchBadge
        match={makeMatchSummary({
          overall_match: null, job_match: null, recommendation: "INSUFFICIENT_DATA",
          recommendation_label: "Not enough data", band: null,
        })}
      />
    );
    expect(screen.getByText("Not enough data")).toBeInTheDocument();
    expect(screen.queryByText(/Overall match/)).not.toBeInTheDocument();
  });
});
