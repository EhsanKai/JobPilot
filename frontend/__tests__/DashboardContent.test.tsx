import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardContent } from "@/components/dashboard/DashboardContent";

const getProfileMock = vi.fn();
const getCurrentResumeMock = vi.fn();
const getPreferencesMock = vi.fn();

vi.mock("@/lib/api/profile", () => ({
  getProfile: () => getProfileMock(),
}));
vi.mock("@/lib/api/resumes", () => ({
  getCurrentResume: () => getCurrentResumeMock(),
}));
vi.mock("@/lib/api/preferences", () => ({
  getPreferences: () => getPreferencesMock(),
}));

describe("DashboardContent", () => {
  beforeEach(() => {
    getProfileMock.mockResolvedValue({
      id: "user-1",
      email: "jane@example.com",
      first_name: "Jane",
      last_name: "Doe",
      profile: {
        id: "profile-1",
        user_id: "user-1",
        phone: "+49 123",
        location: "Berlin",
        linkedin_url: "https://linkedin.com/in/jane",
        current_position: "Engineer",
        years_of_experience: 5,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    });
    getCurrentResumeMock.mockResolvedValue(null);
    getPreferencesMock.mockResolvedValue({
      id: "pref-1",
      user_id: "user-1",
      desired_job_titles: [],
      preferred_locations: [],
      employment_types: [],
      industries: [],
      languages: [],
      minimum_salary: null,
      desired_salary: null,
      salary_currency: null,
      seniority: null,
      remote_preference: null,
      relocation_willingness: false,
      travel_willingness: null,
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });
  });

  it("greets the user by first name", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(screen.getByText("Welcome back, Jane")).toBeInTheDocument();
    await screen.findByText("100%");
  });

  it("lists upcoming features as not-yet-available, clearly labeled", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(screen.getByText("Job Search")).toBeInTheDocument();
    expect(screen.getByText("Coming in V0.3")).toBeInTheDocument();
    expect(screen.getByText("AI Matching")).toBeInTheDocument();
    expect(screen.getByText("Application Assistant")).toBeInTheDocument();
    expect(screen.getByText("Application Tracking")).toBeInTheDocument();
    await screen.findByText("100%");
  });

  it("renders the status widgets", async () => {
    render(<DashboardContent firstName="Jane" />);
    expect(screen.getByText("Profile completion")).toBeInTheDocument();
    expect(screen.getByText("Resume status")).toBeInTheDocument();
    expect(screen.getByText("Job preferences")).toBeInTheDocument();
    await screen.findByText("100%");
  });
});
