import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MyApplications } from "@/components/tracking/MyApplications";
import { ApiError } from "@/lib/http";
import type { ApplicationList, ApplicationListQuery } from "@/types/application";

import { makeSubmissionSummary, makeSummary } from "./fixtures/application";

const listMock = vi.fn();

vi.mock("@/lib/api/tracking", () => ({
  listMyApplications: (...args: unknown[]) => listMock(...args),
}));

function listOf(items: ReturnType<typeof makeSummary>[], overrides: Partial<ApplicationList> = {}) {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 20,
    total_pages: items.length ? 1 : 0,
    ...overrides,
  } satisfies ApplicationList;
}

function lastQuery(): ApplicationListQuery {
  return listMock.mock.calls.at(-1)?.[0] as ApplicationListQuery;
}

const APPLIED = makeSummary({
  id: "app-1",
  status: "APPLIED",
  applied_at: "2026-10-07T09:00:09Z",
  last_status_change_at: "2026-10-08T10:00:00Z",
  follow_up_at: "2026-10-21T09:00:00Z",
  submission: makeSubmissionSummary(),
});
const PREPARING = makeSummary({
  id: "app-2",
  status: "PREPARING",
  company_name: "Globex",
  title: "Data Engineer",
  source: "lever",
  original_url: "https://jobs.lever.co/globex/abc",
});

describe("MyApplications", () => {
  beforeEach(() => {
    listMock.mockReset();
    listMock.mockResolvedValue(listOf([APPLIED, PREPARING]));
  });

  it("shows what each application needs: company, position, status, dates, source, link", async () => {
    render(<MyApplications />);
    const rows = await screen.findAllByRole("listitem");
    const first = within(rows[0]!);

    expect(first.getByRole("link", { name: "Acme GmbH — Senior Backend Engineer (Python)" })).toHaveAttribute(
      "href",
      "/applications/app-1/tracking"
    );
    expect(first.getByText("Applied", { selector: "[data-status] *, [data-status]" })).toBeInTheDocument();
    expect(first.getByText("7 Oct 2026")).toBeInTheDocument(); // application date
    expect(first.getByText("8 Oct 2026")).toBeInTheDocument(); // last status change
    expect(first.getByText("Follow up on")).toBeInTheDocument();
    expect(first.getByText("21 Oct 2026")).toBeInTheDocument();
    expect(first.getByText(/Greenhouse/)).toBeInTheDocument();
    expect(first.getByText("Submitted successfully · confirmation ABC-123")).toBeInTheDocument();
    expect(first.getByRole("link", { name: /Original job posting/ })).toHaveAttribute(
      "href",
      "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x"
    );
    expect(first.getByRole("link", { name: "Open workspace" })).toHaveAttribute(
      "href",
      "/applications/app-1"
    );
  });

  it("opens the original posting externally and safely", async () => {
    render(<MyApplications />);
    const [link] = await screen.findAllByRole("link", { name: /Original job posting/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("does not make a link out of an unsafe original URL", async () => {
    listMock.mockResolvedValue(
      listOf([makeSummary({ original_url: "javascript:alert(1)" })])
    );
    render(<MyApplications />);
    await screen.findByRole("list", { name: "Applications" });
    expect(screen.queryByRole("link", { name: /Original job posting/ })).not.toBeInTheDocument();
  });

  it("says 'Not applied yet' for an application that was never submitted", async () => {
    render(<MyApplications />);
    const rows = await screen.findAllByRole("listitem");
    expect(within(rows[1]!).getByText("Not applied yet")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText("Follow up on")).not.toBeInTheDocument();
    expect(within(rows[1]!).queryByText("Submission")).not.toBeInTheDocument();
  });

  it("asks for the default list first: newest update first, no filters", async () => {
    render(<MyApplications />);
    await screen.findAllByRole("listitem");
    expect(lastQuery()).toMatchObject({ page: 1, statuses: [], company: "", sort: "updated" });
  });

  it("shows an uncertain submission as needing verification, never as a success", async () => {
    listMock.mockResolvedValue(
      listOf([
        makeSummary({
          submission: makeSubmissionSummary({
            result: "SUBMISSION_UNKNOWN",
            needs_verification: true,
            confirmation_reference: null,
          }),
        }),
      ])
    );
    render(<MyApplications />);
    expect(
      await screen.findByText("Submission result uncertain — user verification required")
    ).toBeInTheDocument();
    expect(screen.queryByText(/Submitted successfully/)).not.toBeInTheDocument();
  });

  describe("states", () => {
    it("shows a loading state", async () => {
      listMock.mockReturnValue(new Promise(() => {}));
      render(<MyApplications />);
      expect(screen.getByRole("status")).toHaveTextContent("Loading your applications...");
    });

    it("shows an empty state that points to the job search", async () => {
      listMock.mockResolvedValue(listOf([]));
      render(<MyApplications />);
      expect(await screen.findByText("You haven't started an application yet.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Find jobs" })).toHaveAttribute("href", "/jobs");
    });

    it("shows a different empty state when filters match nothing", async () => {
      const user = userEvent.setup();
      render(<MyApplications />);
      await screen.findAllByRole("listitem");
      listMock.mockResolvedValue(listOf([]));
      await user.selectOptions(screen.getByLabelText("Status"), "OFFER");
      expect(await screen.findByText("No applications match your filters.")).toBeInTheDocument();
      expect(screen.queryByText("You haven't started an application yet.")).not.toBeInTheDocument();
    });

    it("shows an error with a way to try again", async () => {
      const user = userEvent.setup();
      listMock.mockRejectedValueOnce(new ApiError(500, "SERVER_ERROR", "boom", {}));
      render(<MyApplications />);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Unable to load your applications. Please try again."
      );
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findAllByRole("listitem")).toHaveLength(2);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("explains invalid filters", async () => {
      listMock.mockRejectedValueOnce(new ApiError(422, "VALIDATION_ERROR", "bad", {}));
      render(<MyApplications />);
      expect(await screen.findByRole("alert")).toHaveTextContent(/filters aren't valid/);
    });
  });

  describe("filters and sorting", () => {
    it("filters by status at once, from the first page", async () => {
      const user = userEvent.setup();
      render(<MyApplications />);
      await screen.findAllByRole("listitem");

      await user.selectOptions(screen.getByLabelText("Status"), "INTERVIEW");

      await waitFor(() => expect(lastQuery().statuses).toEqual(["INTERVIEW"]));
      expect(lastQuery().page).toBe(1);
    });

    it("offers every status", async () => {
      render(<MyApplications />);
      await screen.findAllByRole("listitem");
      const options = within(screen.getByLabelText("Status")).getAllByRole("option");
      expect(options.map((option) => option.textContent)).toEqual([
        "All statuses",
        "Saved",
        "In preparation",
        "Applied",
        "Follow-up",
        "Interview",
        "Offer",
        "Rejected",
        "Withdrawn",
        "No response",
      ]);
    });

    it("filters by company, source and date range when applied", async () => {
      const user = userEvent.setup();
      render(<MyApplications />);
      await screen.findAllByRole("listitem");
      const callsBefore = listMock.mock.calls.length;

      await user.type(screen.getByLabelText("Company"), "acme");
      await user.type(screen.getByLabelText("Source"), "lever");
      await user.type(screen.getByLabelText("Applied from"), "2026-03-01");
      await user.type(screen.getByLabelText("Applied to"), "2026-03-31");
      // Typing alone asks for nothing: the list changes when the filters are applied.
      expect(listMock.mock.calls.length).toBe(callsBefore);

      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      await waitFor(() =>
        expect(lastQuery()).toMatchObject({
          company: "acme",
          source: "lever",
          appliedFrom: "2026-03-01",
          appliedTo: "2026-03-31",
          page: 1,
        })
      );
      expect(await screen.findByText(/match your filters/)).toBeInTheDocument();
    });

    it("sorts by last updated or application date", async () => {
      const user = userEvent.setup();
      render(<MyApplications />);
      await screen.findAllByRole("listitem");
      expect(within(screen.getByLabelText("Sort by")).getAllByRole("option").map((o) => o.textContent)).toEqual([
        "Last updated",
        "Application date",
      ]);

      await user.selectOptions(screen.getByLabelText("Sort by"), "applied");

      await waitFor(() => expect(lastQuery().sort).toBe("applied"));
    });

    it("clears all filters", async () => {
      const user = userEvent.setup();
      render(<MyApplications />);
      await screen.findAllByRole("listitem");
      await user.selectOptions(screen.getByLabelText("Status"), "REJECTED");
      await user.type(screen.getByLabelText("Company"), "acme");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));
      await waitFor(() => expect(lastQuery().company).toBe("acme"));

      await user.click(screen.getAllByRole("button", { name: "Clear filters" })[0]!);

      await waitFor(() => expect(lastQuery()).toMatchObject({ statuses: [], company: "" }));
      expect(screen.getByLabelText("Company")).toHaveValue("");
      expect(screen.getByLabelText("Status")).toHaveValue("");
    });
  });

  describe("paging", () => {
    it("pages through a long list and goes back to page 1 when the filters change", async () => {
      const user = userEvent.setup();
      listMock.mockResolvedValue(
        listOf([APPLIED], { total: 45, total_pages: 3, page: 1 })
      );
      render(<MyApplications />);
      await screen.findByText("Page 1 of 3");
      expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

      listMock.mockResolvedValue(listOf([PREPARING], { total: 45, total_pages: 3, page: 2 }));
      await user.click(screen.getByRole("button", { name: "Next" }));
      await screen.findByText("Page 2 of 3");
      expect(lastQuery().page).toBe(2);

      await user.selectOptions(screen.getByLabelText("Status"), "APPLIED");
      await waitFor(() => expect(lastQuery()).toMatchObject({ page: 1, statuses: ["APPLIED"] }));
    });

    it("shows no pager for a single page", async () => {
      render(<MyApplications />);
      await screen.findAllByRole("listitem");
      expect(screen.queryByRole("navigation", { name: "Pages" })).not.toBeInTheDocument();
    });
  });
});
