import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DesiredRolesManager } from "@/components/profile/DesiredRolesManager";

const addJobTitleMock = vi.fn();
const deleteJobTitleMock = vi.fn();
vi.mock("@/lib/api/preferences", () => ({
  addJobTitle: (...args: unknown[]) => addJobTitleMock(...args),
  deleteJobTitle: (...args: unknown[]) => deleteJobTitleMock(...args),
}));

describe("DesiredRolesManager", () => {
  beforeEach(() => {
    addJobTitleMock.mockReset();
    deleteJobTitleMock.mockReset();
  });

  it("shows an empty state and adds a new primary job title", async () => {
    addJobTitleMock.mockResolvedValue({
      id: "jt-1",
      title: "SAP ABAP Developer",
      priority: "primary",
    });
    const onChange = vi.fn();

    const user = userEvent.setup();
    render(<DesiredRolesManager jobTitles={[]} onChange={onChange} />);

    expect(screen.getByText(/no desired job titles added yet/i)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/add a desired job title/i), "SAP ABAP Developer");
    await user.selectOptions(screen.getByLabelText(/priority/i), "primary");
    await user.click(screen.getByRole("button", { name: /add title/i }));

    await waitFor(() =>
      expect(addJobTitleMock).toHaveBeenCalledWith({
        title: "SAP ABAP Developer",
        priority: "primary",
      })
    );
    expect(onChange).toHaveBeenCalledWith([
      { id: "jt-1", title: "SAP ABAP Developer", priority: "primary" },
    ]);
  });

  it("removes a job title", async () => {
    deleteJobTitleMock.mockResolvedValue(undefined);
    const onChange = vi.fn();

    const user = userEvent.setup();
    render(
      <DesiredRolesManager
        jobTitles={[{ id: "jt-1", title: "Backend Engineer", priority: "secondary" }]}
        onChange={onChange}
      />
    );

    await user.click(screen.getByRole("button", { name: /remove/i }));

    await waitFor(() => expect(deleteJobTitleMock).toHaveBeenCalledWith("jt-1"));
    expect(onChange).toHaveBeenCalledWith([]);
  });
});
