import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PreferencesForm } from "@/components/profile/PreferencesForm";
import type { JobPreferences } from "@/types/preferences";

const updatePreferencesMock = vi.fn();
vi.mock("@/lib/api/preferences", () => ({
  updatePreferences: (...args: unknown[]) => updatePreferencesMock(...args),
}));

const basePreferences: JobPreferences = {
  id: "pref-1",
  user_id: "user-1",
  desired_job_titles: [{ id: "jt-1", title: "Backend Engineer", priority: "primary" }],
  preferred_locations: [{ id: "loc-1", location: "Berlin", radius_km: null }],
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
};

describe("PreferencesForm", () => {
  beforeEach(() => {
    updatePreferencesMock.mockReset();
  });

  it("renders deal breaker checkboxes, all unchecked by default", () => {
    render(<PreferencesForm preferences={basePreferences} onSaved={vi.fn()} />);

    expect(screen.getByLabelText("Salary below minimum")).not.toBeChecked();
    expect(screen.getByLabelText("Fully on-site")).not.toBeChecked();
    expect(screen.getByLabelText("Required relocation")).not.toBeChecked();
  });

  it("submits selected work model, employment type, and deal breakers without wiping desired job titles or locations", async () => {
    updatePreferencesMock.mockResolvedValue({ ...basePreferences });
    const onSaved = vi.fn();

    const user = userEvent.setup();
    render(<PreferencesForm preferences={basePreferences} onSaved={onSaved} />);

    await user.click(screen.getByLabelText("remote"));
    await user.click(screen.getByLabelText("full time"));
    await user.click(screen.getByLabelText("Salary below minimum"));
    await user.click(screen.getByRole("button", { name: /save preferences/i }));

    await waitFor(() => expect(updatePreferencesMock).toHaveBeenCalled());
    expect(updatePreferencesMock.mock.calls[0]).toBeDefined();
    const payload = updatePreferencesMock.mock.calls[0]![0];

    expect(payload.work_models).toContain("remote");
    expect(payload.employment_types).toContain("full_time");
    expect(payload.deal_breakers).toContainEqual({ type: "minimum_salary", enabled: true });
    expect(payload.desired_job_titles).toEqual([
      { title: "Backend Engineer", priority: "primary" },
    ]);
    expect(payload.preferred_locations).toEqual([{ location: "Berlin", radius_km: null }]);

    expect(await screen.findByText(/preferences saved successfully/i)).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalled();
  });

  it("shows the server's validation message when salary is inconsistent", async () => {
    const { ApiError } = await import("@/lib/http");
    updatePreferencesMock.mockRejectedValue(
      new ApiError(
        422,
        "VALIDATION_ERROR",
        "desired_salary must be greater than or equal to minimum_salary",
        {}
      )
    );

    const user = userEvent.setup();
    render(<PreferencesForm preferences={basePreferences} onSaved={vi.fn()} />);

    await user.type(screen.getByLabelText("Minimum salary"), "70000");
    await user.type(screen.getByLabelText("Desired salary"), "50000");
    await user.click(screen.getByRole("button", { name: /save preferences/i }));

    expect(
      await screen.findByText(/desired_salary must be greater than or equal to minimum_salary/i)
    ).toBeInTheDocument();
  });
});
