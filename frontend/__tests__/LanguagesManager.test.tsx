import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguagesManager } from "@/components/profile/LanguagesManager";

const getLanguagesMock = vi.fn();
const addLanguageMock = vi.fn();
const deleteLanguageMock = vi.fn();
vi.mock("@/lib/api/languages", () => ({
  getLanguages: () => getLanguagesMock(),
  addLanguage: (...args: unknown[]) => addLanguageMock(...args),
  deleteLanguage: (...args: unknown[]) => deleteLanguageMock(...args),
}));

describe("LanguagesManager", () => {
  beforeEach(() => {
    getLanguagesMock.mockReset();
    addLanguageMock.mockReset();
    deleteLanguageMock.mockReset();
  });

  it("shows an empty state when there are no languages", async () => {
    getLanguagesMock.mockResolvedValue([]);
    render(<LanguagesManager />);
    expect(await screen.findByText(/no languages added yet/i)).toBeInTheDocument();
  });

  it("adds a language and shows it in the list", async () => {
    getLanguagesMock.mockResolvedValue([]);
    addLanguageMock.mockResolvedValue({ id: "lang-1", name: "German", proficiency: "fluent" });

    const user = userEvent.setup();
    render(<LanguagesManager />);
    await screen.findByText(/no languages added yet/i);

    await user.type(screen.getByLabelText(/add a language/i), "German");
    await user.click(screen.getByRole("button", { name: /add language/i }));

    await waitFor(() => expect(addLanguageMock).toHaveBeenCalled());
    const item = await screen.findByRole("listitem");
    expect(item).toHaveTextContent("German");
    expect(item).toHaveTextContent("fluent");
  });

  it("removes a language from the list", async () => {
    getLanguagesMock.mockResolvedValue([
      { id: "lang-1", name: "English", proficiency: "native" },
    ]);
    deleteLanguageMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<LanguagesManager />);
    await screen.findByText(/English/);

    await user.click(screen.getByRole("button", { name: /remove/i }));

    await waitFor(() => expect(deleteLanguageMock).toHaveBeenCalledWith("lang-1"));
    expect(await screen.findByText(/no languages added yet/i)).toBeInTheDocument();
  });
});
