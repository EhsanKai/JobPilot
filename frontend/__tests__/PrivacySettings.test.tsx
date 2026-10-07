import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PrivacySettings } from "@/components/privacy/PrivacySettings";
import type { PrivacyOverview, PrivacySettings as Settings } from "@/types/privacy";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock }),
}));

const clearStoredTokenMock = vi.fn();
vi.mock("@/lib/auth", () => ({
  getStoredToken: () => "token",
  setStoredToken: vi.fn(),
  clearStoredToken: () => clearStoredTokenMock(),
}));

const api = {
  getPrivacyOverview: vi.fn(),
  getPrivacySettings: vi.fn(),
  getConsentHistory: vi.fn(),
  updatePrivacySettings: vi.fn(),
  downloadMyData: vi.fn(),
  deleteMyAccount: vi.fn(),
  logoutEverywhere: vi.fn(),
};
vi.mock("@/lib/api/privacy", () => ({
  DELETE_CONFIRMATION_PHRASE: "DELETE MY ACCOUNT",
  getPrivacyOverview: () => api.getPrivacyOverview(),
  getPrivacySettings: () => api.getPrivacySettings(),
  getConsentHistory: () => api.getConsentHistory(),
  updatePrivacySettings: (v: boolean) => api.updatePrivacySettings(v),
  downloadMyData: () => api.downloadMyData(),
  deleteMyAccount: (p: string, c: string) => api.deleteMyAccount(p, c),
  logoutEverywhere: () => api.logoutEverywhere(),
}));

const overview: PrivacyOverview = {
  categories: [
    {
      key: "profile",
      title: "Profile",
      purpose: "Your profile.",
      retention: "Until you delete it.",
      records: { user_profiles: 1, user_skills: 4 },
    },
  ],
  file_storage: "Files are stored per account.",
  browser_storage: [
    { name: "jobpilot_access_token", type: "localStorage", purpose: "Sign-in", strictly_necessary: true },
  ],
  cookies: [],
  optional_tracking: [],
  recipients: [
    { name: "Anthropic (Claude API)", role: "External AI provider", data: "Resume text", purpose: "AI", control: "Switch off." },
  ],
  legal_notice: "Technical controls; legal review required.",
};

const settings: Settings = {
  external_ai_processing_allowed: true,
  ai_provider: "anthropic",
  ai_model: null,
  ai_configured: true,
  policy_version: "2026-10-ai-v1",
  contact_details_pseudonymized: true,
};

describe("PrivacySettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    replaceMock.mockReset();
    clearStoredTokenMock.mockReset();
    api.getPrivacyOverview.mockResolvedValue(overview);
    api.getPrivacySettings.mockResolvedValue(settings);
    api.getConsentHistory.mockResolvedValue([]);
  });

  it("shows what is stored, the recipients and the legal notice", async () => {
    render(<PrivacySettings />);
    expect(await screen.findByTestId("data-category-profile")).toHaveTextContent("5 records");
    expect(screen.getByText(/Anthropic \(Claude API\)/)).toBeInTheDocument();
    expect(screen.getByText("Technical controls; legal review required.")).toBeInTheDocument();
    expect(screen.getByText(/none \(no analytics, no advertising\)/)).toBeInTheDocument();
  });

  it("switches external AI processing off and shows the recorded decision", async () => {
    api.updatePrivacySettings.mockResolvedValue({ ...settings, external_ai_processing_allowed: false });
    api.getConsentHistory.mockResolvedValueOnce([]).mockResolvedValueOnce([
      { category: "external_ai_processing", policy_version: "2026-10-ai-v1", granted: false, decided_at: "2026-10-07T10:00:00Z" },
    ]);
    render(<PrivacySettings />);
    const toggle = await screen.findByRole("checkbox", { name: "Allow external AI processing" });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    expect(api.updatePrivacySettings).toHaveBeenCalledWith(false);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(await screen.findByTestId("consent-history")).toHaveTextContent("switched off");
  });

  it("downloads the export", async () => {
    api.downloadMyData.mockResolvedValue(new Blob(["zip"], { type: "application/zip" }));
    const createObjectURL = vi.fn(() => "blob:export");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<PrivacySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Download my data" }));
    await waitFor(() => expect(api.downloadMyData).toHaveBeenCalled());
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    click.mockRestore();
  });

  it("deletes only after password and the exact confirmation phrase", async () => {
    api.deleteMyAccount.mockResolvedValue({ deleted: true, deleted_records: {}, deleted_files: 0, file_cleanup_complete: true, kept: [] });
    render(<PrivacySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "I want to delete my account" }));
    const submit = screen.getByRole("button", { name: "Permanently delete my account" });
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Your password"), "secret-password");
    await userEvent.type(screen.getByLabelText(/to confirm/), "delete my account");
    expect(submit).toBeDisabled(); // the phrase must match exactly
    await userEvent.clear(screen.getByLabelText(/to confirm/));
    await userEvent.type(screen.getByLabelText(/to confirm/), "DELETE MY ACCOUNT");
    expect(submit).toBeEnabled();

    await userEvent.click(submit);
    expect(api.deleteMyAccount).toHaveBeenCalledWith("secret-password", "DELETE MY ACCOUNT");
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login?account=deleted"));
    expect(clearStoredTokenMock).toHaveBeenCalled();
  });

  it("keeps the account and shows the error when the password is wrong", async () => {
    const { ApiError } = await import("@/lib/http");
    api.deleteMyAccount.mockRejectedValue(new ApiError(403, "INVALID_PASSWORD", "The password is not correct.", {}));
    render(<PrivacySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "I want to delete my account" }));
    await userEvent.type(screen.getByLabelText("Your password"), "wrong");
    await userEvent.type(screen.getByLabelText(/to confirm/), "DELETE MY ACCOUNT");
    await userEvent.click(screen.getByRole("button", { name: "Permanently delete my account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The password is not correct.");
    expect(replaceMock).not.toHaveBeenCalled();
    expect(clearStoredTokenMock).not.toHaveBeenCalled();
  });

  it("logs out everywhere and leaves", async () => {
    api.logoutEverywhere.mockResolvedValue(undefined);
    render(<PrivacySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Log out everywhere" }));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(api.logoutEverywhere).toHaveBeenCalled();
    expect(clearStoredTokenMock).toHaveBeenCalled();
  });
});
