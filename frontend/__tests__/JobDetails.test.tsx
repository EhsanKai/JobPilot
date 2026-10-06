import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobCard } from "@/components/jobs/JobCard";
import { JobDetails } from "@/components/jobs/JobDetails";
import { ApiError } from "@/lib/http";

import { makeJob, makeJobDetail } from "./fixtures/jobs";

const getJobMock = vi.fn();
const saveJobMock = vi.fn();
const unsaveJobMock = vi.fn();

vi.mock("@/lib/api/jobs", () => ({
  getJob: (...args: unknown[]) => getJobMock(...args),
  saveJob: (...args: unknown[]) => saveJobMock(...args),
  unsaveJob: (...args: unknown[]) => unsaveJobMock(...args),
}));

// The match panel (V0.6) has its own tests (MatchPanel.test.tsx).
vi.mock("@/lib/api/match", () => ({
  getMatch: (jobId: string) =>
    Promise.resolve({ job_id: jobId, status: "not_calculated", result: null }),
  calculateMatch: vi.fn(),
  explainMatch: vi.fn(),
}));

describe("JobDetails", () => {
  beforeEach(() => {
    getJobMock.mockReset();
    saveJobMock.mockReset();
    unsaveJobMock.mockReset();
    getJobMock.mockResolvedValue(makeJobDetail());
  });

  it("shows a loading state, then the job", async () => {
    render(<JobDetails jobId="job-1" />);
    expect(screen.getByText("Loading job...")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Senior Backend Engineer (Python)" })
    ).toBeInTheDocument();
    expect(getJobMock).toHaveBeenCalledWith("job-1");
  });

  it("separates JobPilot data from the original posting", async () => {
    render(<JobDetails jobId="job-1" />);
    const jobPilot = (await screen.findByRole("heading", { name: "JobPilot data" })).closest("div")!;
    expect(within(jobPilot).getByText("€70,000 – €90,000 per year")).toBeInTheDocument();
    expect(within(jobPilot).getByText("Hybrid")).toBeInTheDocument();
    expect(within(jobPilot).getByText("Full-time")).toBeInTheDocument();
    expect(within(jobPilot).getByText("Senior")).toBeInTheDocument();
    expect(within(jobPilot).getByText("Greenhouse (acme)")).toBeInTheDocument();
    expect(within(jobPilot).getByRole("list", { name: "Technologies" })).toHaveTextContent(
      "PythonPostgreSQL"
    );

    const posting = screen.getByRole("heading", { name: "Original job posting" }).closest("div")!;
    expect(within(posting).getByText("Design and build Python services")).toBeInTheDocument();
    expect(within(posting).getByText("5+ years of backend experience")).toBeInTheDocument();
    expect(within(posting).getByText(/We build logistics software/)).toBeInTheDocument();
  });

  it("links to the original posting in a new tab", async () => {
    render(<JobDetails jobId="job-1" />);
    const link = await screen.findByRole("link", { name: "View original posting ↗" });
    expect(link).toHaveAttribute("href", "https://job-boards.greenhouse.io/acme/jobs/4012345");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("marks values the posting doesn't state", async () => {
    getJobMock.mockResolvedValue(
      makeJobDetail({ salary_min: null, salary_max: null, work_model: null, description: null })
    );
    render(<JobDetails jobId="job-1" />);
    await screen.findByRole("heading", { name: "JobPilot data" });
    expect(screen.getAllByText("Not stated").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/didn't provide a description/)).toBeInTheDocument();
  });

  it("shows a not-found state", async () => {
    getJobMock.mockRejectedValue(new ApiError(404, "NOT_FOUND", "Job not found.", {}));
    render(<JobDetails jobId="missing" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This job doesn't exist or isn't in your job list."
    );
    expect(screen.getByRole("link", { name: "← Back to jobs" })).toHaveAttribute("href", "/jobs");
  });

  it("saves the job", async () => {
    const user = userEvent.setup();
    saveJobMock.mockResolvedValue(makeJobDetail({ saved: true, saved_at: "2026-10-02T00:00:00Z" }));
    render(<JobDetails jobId="job-1" />);
    await user.click(await screen.findByRole("button", { name: "Save" }));
    expect(saveJobMock).toHaveBeenCalledWith("job-1");
    expect(await screen.findByRole("button", { name: "Saved ✓" })).toBeInTheDocument();
  });

  it("never renders unsafe links", async () => {
    getJobMock.mockResolvedValue(
      makeJobDetail({ canonical_url: "javascript:alert(1)", apply_url: "javascript:alert(2)" })
    );
    render(<JobDetails jobId="job-1" />);
    await screen.findByRole("heading", { name: "JobPilot data" });
    for (const link of screen.queryAllByRole("link")) {
      expect(link.getAttribute("href")).not.toMatch(/^javascript:/);
    }
    expect(screen.queryByRole("link", { name: "View original posting ↗" })).not.toBeInTheDocument();
  });
});

describe("JobCard", () => {
  it("shows source, posted date and a View Job link to the original posting", () => {
    render(<JobCard job={makeJob({ source: "lever" })} onToggleSave={() => {}} />);
    expect(screen.getByText("via Lever")).toBeInTheDocument();
    const view = screen.getByRole("link", { name: "View Job ↗" });
    expect(view).toHaveAttribute("href", "https://job-boards.greenhouse.io/acme/jobs/4012345");
    expect(view).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Details" })).toHaveAttribute("href", "/jobs/job-1");
  });

  it("handles missing optional data", () => {
    render(
      <JobCard
        job={makeJob({
          location: null,
          work_model: null,
          employment_type: null,
          salary_min: null,
          salary_max: null,
          posted_at: null,
        })}
        onToggleSave={() => {}}
      />
    );
    expect(screen.queryByRole("list", { name: "Job facts" })).not.toBeInTheDocument();
    expect(screen.getByText("Posting date not stated")).toBeInTheDocument();
  });

  it("calls onToggleSave", async () => {
    const onToggleSave = vi.fn();
    const job = makeJob();
    render(<JobCard job={job} onToggleSave={onToggleSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onToggleSave).toHaveBeenCalledWith(job);
  });
});
