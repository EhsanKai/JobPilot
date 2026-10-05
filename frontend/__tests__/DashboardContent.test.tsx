import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardContent } from "@/components/dashboard/DashboardContent";

import { makeResume } from "./fixtures/resumeProfile";

const getProfileCompletenessMock = vi.fn();
const getCurrentResumeMock = vi.fn();
const getPreferencesMock = vi.fn();

vi.mock("@/lib/api/completeness", () => ({
  getProfileCompleteness: () => getProfileCompletenessMock(),
}));
vi.mock("@/lib/api/resumes", () => ({
  getCurrentResume: () => getCurrentResumeMock(),
}));
vi.mock("@/lib/api/preferences", () => ({
  getPreferences: () => getPreferencesMock(),
}));

describe("DashboardContent", () => {
  beforeEach(() => {
    getProfileCompletenessMock.mockResolvedValue({
      percentage: 42,
      categories: [],
      message: "Keep going -- add a few more details to strengthen your profile.",
    });
    getCurrentResumeMock.mockResolvedValue(null);
    getPreferencesMock.mockResolvedValue({
      id: "pref-1",
      user_id: "user-1",
      desired_job_titles: [],
      preferred_locations: [],
      employment_types: [],
      preferred_industries: [],
      work_models: [],
      minimum_salary: null,
      desired_salary: null,
      salary_currency: null,
      relocation: null,
      travel_willingness: null,
      deal_breakers: [],
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });
  });

  it("greets the user by first name", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(screen.getByText("Welcome back, Jane")).toBeInTheDocument();
    await screen.findByText("42%");
  });

  it("lists upcoming features as not-yet-available, clearly labeled", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(screen.queryByText("Coming in V0.3")).not.toBeInTheDocument();
    expect(screen.getByText("AI Matching")).toBeInTheDocument();
    expect(screen.getByText("Application Assistant")).toBeInTheDocument();
    expect(screen.getByText("Application Tracking")).toBeInTheDocument();
    await screen.findByText("42%");
  });

  it("links to Job Search, which is available since V0.3", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(screen.getByText("Job Search")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Find jobs" })).toHaveAttribute("href", "/jobs");
    await screen.findByText("42%");
  });

  it("renders the status widgets", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(await screen.findByText("Profile completeness")).toBeInTheDocument();
    expect(screen.getByText("Resume status")).toBeInTheDocument();
    expect(screen.getByText("Job preferences")).toBeInTheDocument();
    await screen.findByText("42%");
  });

  it("prompts to upload a resume when there is none", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(await screen.findByText("Not uploaded")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upload Resume/ })).toHaveAttribute(
      "href",
      "/profile/resume"
    );
  });

  it("shows that an analyzed resume still needs review", async () => {
    getCurrentResumeMock.mockResolvedValue(makeResume({ status: "needs_review" }));
    render(<DashboardContent firstName="Jane" />);

    const progress = await screen.findByRole("list", { name: "Resume progress" });
    expect(progress).toHaveTextContent("Uploaded");
    expect(progress).toHaveTextContent("Analyzed");
    expect(progress).toHaveTextContent("Review required");
    expect(screen.getByRole("link", { name: /Review Resume/ })).toHaveAttribute(
      "href",
      "/profile/resume/review"
    );
  });

  it("shows a confirmed resume profile", async () => {
    getCurrentResumeMock.mockResolvedValue(makeResume({ status: "confirmed" }));
    render(<DashboardContent firstName="Jane" />);

    const progress = await screen.findByRole("list", { name: "Resume progress" });
    expect(progress).toHaveTextContent("Confirmed");
    expect(screen.getByRole("link", { name: /View Resume Profile/ })).toBeInTheDocument();
  });

  it("offers analysis for an uploaded but unanalyzed resume", async () => {
    getCurrentResumeMock.mockResolvedValue(makeResume({ status: "uploaded" }));
    render(<DashboardContent firstName="Jane" />);

    expect(await screen.findByRole("link", { name: /Analyze Resume/ })).toBeInTheDocument();
  });

  it("surfaces a failed analysis", async () => {
    getCurrentResumeMock.mockResolvedValue(makeResume({ status: "ocr_required" }));
    render(<DashboardContent firstName="Jane" />);

    expect(await screen.findByText(/OCR required/)).toBeInTheDocument();
  });
});
