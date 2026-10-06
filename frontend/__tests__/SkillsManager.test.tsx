import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SkillsManager } from "@/components/profile/SkillsManager";

const getSkillsMock = vi.fn();
const addSkillMock = vi.fn();
const deleteSkillMock = vi.fn();
vi.mock("@/lib/api/skills", () => ({
  getSkills: () => getSkillsMock(),
  addSkill: (...args: unknown[]) => addSkillMock(...args),
  updateSkill: vi.fn(),
  deleteSkill: (...args: unknown[]) => deleteSkillMock(...args),
}));

describe("SkillsManager", () => {
  beforeEach(() => {
    getSkillsMock.mockReset();
    addSkillMock.mockReset();
    deleteSkillMock.mockReset();
  });

  it("shows an empty state when there are no skills", async () => {
    getSkillsMock.mockResolvedValue([]);
    render(<SkillsManager />);
    expect(await screen.findByText(/no skills added yet/i)).toBeInTheDocument();
  });

  it("adds a skill and shows it in the list", async () => {
    getSkillsMock.mockResolvedValue([]);
    addSkillMock.mockResolvedValue({
      id: "skill-1",
      name: "SAP ABAP",
      proficiency: "advanced",
      years_of_experience: 5,
    });

    const user = userEvent.setup();
    render(<SkillsManager />);
    await screen.findByText(/no skills added yet/i);

    await user.type(screen.getByLabelText(/add a skill/i), "SAP ABAP");
    await user.click(screen.getByRole("button", { name: /add skill/i }));

    await waitFor(() => expect(addSkillMock).toHaveBeenCalled());
    expect(await screen.findByText("SAP ABAP")).toBeInTheDocument();
  });

  it("removes a skill from the list", async () => {
    getSkillsMock.mockResolvedValue([
      { id: "skill-1", name: "Python", proficiency: "expert", years_of_experience: 6 },
    ]);
    deleteSkillMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<SkillsManager />);
    await screen.findByText("Python");

    await user.click(screen.getByRole("button", { name: /remove/i }));

    await waitFor(() => expect(deleteSkillMock).toHaveBeenCalledWith("skill-1"));
    expect(await screen.findByText(/no skills added yet/i)).toBeInTheDocument();
  });
});
