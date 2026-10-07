import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { StatusBadge } from "@/components/tracking/StatusBadge";
import { ApplicationsSummaryCard } from "@/components/tracking/ApplicationsSummaryCard";
import { TrackingSummaryCard } from "@/components/tracking/TrackingSummaryCard";
import { ALL_STATUSES, statusLabel } from "@/lib/tracking";
import type { StatusCounts } from "@/types/application";

import { makeSubmissionSummary, makeTracking } from "./fixtures/application";

const getStatusCountsMock = vi.fn();

vi.mock("@/lib/api/tracking", () => ({
  getStatusCounts: () => getStatusCountsMock(),
}));

describe("StatusBadge", () => {
  it.each(ALL_STATUSES)("%s has a text label, a decorative symbol and a tone", (status) => {
    render(<StatusBadge status={status} />);
    const badge = screen.getByText(statusLabel(status), { exact: false }).closest("[data-status]")!;
    expect(badge).toHaveAttribute("data-status", status);
    expect(badge).toHaveAttribute("data-tone");
    expect(badge).toHaveTextContent(statusLabel(status));
    // The symbol never carries the meaning on its own: it is hidden from assistive technology.
    expect(badge.querySelector('[aria-hidden="true"]')).not.toBeNull();
    // The meaning is available as a tooltip too.
    expect(badge).toHaveAttribute("title");
  });

  it("gives rejected, withdrawn and no response different symbols as well as colours", () => {
    const symbols = (["REJECTED", "WITHDRAWN", "NO_RESPONSE"] as const).map((status) => {
      const { container, unmount } = render(<StatusBadge status={status} />);
      const symbol = container.querySelector('[aria-hidden="true"]')!.textContent;
      unmount();
      return symbol;
    });
    expect(new Set(symbols).size).toBe(3);
  });

  it("can carry an accessible name", () => {
    render(<StatusBadge status="APPLIED" aria-label="Application status" />);
    expect(screen.getByLabelText("Application status")).toHaveTextContent("Applied");
  });
});

describe("TrackingSummaryCard", () => {
  it("shows the status, dates and the way to the timeline", () => {
    render(
      <TrackingSummaryCard
        applicationId="app-1"
        tracking={makeTracking({ status: "FOLLOW_UP", follow_up_at: "2026-10-21T09:00:00Z" })}
      />
    );
    expect(screen.getByLabelText("Tracking status")).toHaveTextContent("Follow-up");
    expect(screen.getAllByText("7 Oct 2026").length).toBeGreaterThan(0);
    expect(screen.getByText("Follow up on")).toBeInTheDocument();
    expect(screen.getByText("21 Oct 2026")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Timeline & status/ })).toHaveAttribute(
      "href",
      "/applications/app-1/tracking"
    );
  });

  it("shows a submitted application's result and date", () => {
    render(
      <TrackingSummaryCard
        applicationId="app-1"
        tracking={makeTracking({ status: "APPLIED", submission: makeSubmissionSummary() })}
      />
    );
    expect(screen.getByLabelText("Tracking status")).toHaveTextContent("Applied");
    expect(screen.getByText("Submitted successfully · confirmation ABC-123")).toBeInTheDocument();
  });

  it("says an unsubmitted application is not applied, and shows no submission", () => {
    render(<TrackingSummaryCard applicationId="app-1" tracking={makeTracking()} />);
    expect(screen.getByText("Not applied yet")).toBeInTheDocument();
    expect(screen.queryByText("Submission")).not.toBeInTheDocument();
    expect(screen.queryByText("Follow up on")).not.toBeInTheDocument();
  });

  it("does not present an uncertain result as applied", () => {
    render(
      <TrackingSummaryCard
        applicationId="app-1"
        tracking={makeTracking({
          status: "PREPARING",
          submission: makeSubmissionSummary({ result: "SUBMISSION_UNKNOWN", needs_verification: true }),
        })}
      />
    );
    expect(screen.getByLabelText("Tracking status")).toHaveTextContent("In preparation");
    expect(
      screen.getByText("Submission result uncertain — user verification required")
    ).toBeInTheDocument();
  });
});

function counts(partial: Partial<StatusCounts["counts"]>): StatusCounts {
  const full = Object.fromEntries(ALL_STATUSES.map((status) => [status, 0])) as StatusCounts["counts"];
  Object.assign(full, partial);
  const total = Object.values(full).reduce((sum, n) => sum + n, 0);
  return { counts: full, total };
}

describe("ApplicationsSummaryCard", () => {
  beforeEach(() => {
    getStatusCountsMock.mockReset();
  });

  it("shows a compact count per status, and a link to My Applications", async () => {
    getStatusCountsMock.mockResolvedValue(
      counts({ APPLIED: 12, INTERVIEW: 3, OFFER: 1, REJECTED: 5, NO_RESPONSE: 2, PREPARING: 4 })
    );
    render(<ApplicationsSummaryCard />);
    const applied = await screen.findByText("Applied");
    expect(applied.nextElementSibling).toHaveTextContent("12");
    expect(screen.getByText("Interview").nextElementSibling).toHaveTextContent("3");
    expect(screen.getByText("Offer").nextElementSibling).toHaveTextContent("1");
    expect(screen.getByText("Rejected").nextElementSibling).toHaveTextContent("5");
    expect(screen.getByText("No response").nextElementSibling).toHaveTextContent("2");
    expect(screen.queryByText("In preparation")).not.toBeInTheDocument(); // only the summary statuses
    expect(screen.getByRole("link", { name: "My Applications" })).toHaveAttribute("href", "/applications");
  });

  it("says so when there are no applications", async () => {
    getStatusCountsMock.mockResolvedValue(counts({}));
    render(<ApplicationsSummaryCard />);
    expect(await screen.findByText("You haven't started an application yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "My Applications" })).toBeInTheDocument();
  });

  it("says how many are in preparation when nothing has been submitted", async () => {
    getStatusCountsMock.mockResolvedValue(counts({ PREPARING: 2 }));
    render(<ApplicationsSummaryCard />);
    expect(await screen.findByText("2 applications, none submitted yet.")).toBeInTheDocument();
  });

  it("still links to the list when the counts fail to load", async () => {
    getStatusCountsMock.mockRejectedValue(new Error("down"));
    render(<ApplicationsSummaryCard />);
    expect(await screen.findByText("Your application counts are unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "My Applications" })).toBeInTheDocument();
  });

  it("shows a loading state first", () => {
    getStatusCountsMock.mockReturnValue(new Promise(() => {}));
    render(<ApplicationsSummaryCard />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });
});
