import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LocationsManager } from "@/components/profile/LocationsManager";

const addLocationMock = vi.fn();
const deleteLocationMock = vi.fn();
vi.mock("@/lib/api/preferences", () => ({
  addLocation: (...args: unknown[]) => addLocationMock(...args),
  deleteLocation: (...args: unknown[]) => deleteLocationMock(...args),
}));

describe("LocationsManager", () => {
  beforeEach(() => {
    addLocationMock.mockReset();
    deleteLocationMock.mockReset();
  });

  it("shows an empty state and adds a new location", async () => {
    addLocationMock.mockResolvedValue({ id: "loc-1", location: "Cologne", radius_km: null });
    const onChange = vi.fn();

    const user = userEvent.setup();
    render(<LocationsManager locations={[]} onChange={onChange} />);

    expect(screen.getByText(/no preferred locations added yet/i)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/add a preferred location/i), "Cologne");
    await user.click(screen.getByRole("button", { name: /add location/i }));

    await waitFor(() =>
      expect(addLocationMock).toHaveBeenCalledWith({ location: "Cologne" })
    );
    expect(onChange).toHaveBeenCalledWith([{ id: "loc-1", location: "Cologne", radius_km: null }]);
  });

  it("removes a location", async () => {
    deleteLocationMock.mockResolvedValue(undefined);
    const onChange = vi.fn();

    const user = userEvent.setup();
    render(
      <LocationsManager
        locations={[{ id: "loc-1", location: "Berlin", radius_km: null }]}
        onChange={onChange}
      />
    );

    await user.click(screen.getByRole("button", { name: /remove berlin/i }));

    await waitFor(() => expect(deleteLocationMock).toHaveBeenCalledWith("loc-1"));
    expect(onChange).toHaveBeenCalledWith([]);
  });
});
