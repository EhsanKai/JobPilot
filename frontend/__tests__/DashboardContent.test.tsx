import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardContent } from "@/components/dashboard/DashboardContent";

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
    expect(screen.getByText("Job Search")).toBeInTheDocument();
    expect(screen.getByText("Coming in V0.3")).toBeInTheDocument();
    expect(screen.getByText("AI Matching")).toBeInTheDocument();
    expect(screen.getByText("Application Assistant")).toBeInTheDocument();
    expect(screen.getByText("Application Tracking")).toBeInTheDocument();
    await screen.findByText("42%");
  });

  it("renders the status widgets", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(await screen.findByText("Profile completeness")).toBeInTheDocument();
    expect(screen.getByText("Resume status")).toBeInTheDocument();
    expect(screen.getByText("Job preferences")).toBeInTheDocument();
    await screen.findByText("42%");
  });
});
