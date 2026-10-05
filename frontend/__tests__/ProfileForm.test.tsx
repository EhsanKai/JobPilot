import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileForm } from "@/components/profile/ProfileForm";

const getProfileMock = vi.fn();
const updateProfileMock = vi.fn();
vi.mock("@/lib/api/profile", () => ({
  getProfile: () => getProfileMock(),
  updateProfile: (...args: unknown[]) => updateProfileMock(...args),
}));

const baseProfile = {
  id: "user-1",
  email: "jane@example.com",
  first_name: "Jane",
  last_name: "Doe",
  phone: null,
  location: null,
  linkedin_url: null,
  current_job_title: null,
  years_of_experience: null,
  seniority_level: null,
  industries: [],
};

describe("ProfileForm", () => {
  beforeEach(() => {
    getProfileMock.mockReset();
    updateProfileMock.mockReset();
  });

  it("shows a loading state before data arrives", () => {
    getProfileMock.mockReturnValue(new Promise(() => {}));
    render(<ProfileForm />);
    expect(screen.getByText(/loading your profile/i)).toBeInTheDocument();
  });

  it("renders the loaded profile fields", async () => {
    getProfileMock.mockResolvedValue(baseProfile);
    render(<ProfileForm />);

    expect(await screen.findByDisplayValue("Jane")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Doe")).toBeInTheDocument();
    expect(screen.getByDisplayValue("jane@example.com")).toBeInTheDocument();
  });

  it("saves changes and shows a success message", async () => {
    getProfileMock.mockResolvedValue(baseProfile);
    updateProfileMock.mockResolvedValue({ ...baseProfile, location: "Berlin" });

    const user = userEvent.setup();
    render(<ProfileForm />);

    await screen.findByDisplayValue("Jane");
    await user.type(screen.getByLabelText("Location"), "Berlin");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateProfileMock).toHaveBeenCalled());
    expect(await screen.findByText(/profile updated successfully/i)).toBeInTheDocument();
  });
});
