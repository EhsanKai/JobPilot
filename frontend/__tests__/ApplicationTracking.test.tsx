import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationTracking } from "@/components/tracking/ApplicationTracking";
import { ApiError } from "@/lib/http";
import type {
  ApplicationStatus,
  StatusChangeRequest,
  StatusChangeResponse,
  TimelineEntry,
} from "@/types/application";

import {
  makeApplication,
  makeSubmissionSummary,
  makeTimelineEntry,
  makeTracking,
} from "./fixtures/application";

const getApplicationMock = vi.fn();
const getTimelineMock = vi.fn();
const changeStatusMock = vi.fn();
const updateTrackingMock = vi.fn();

vi.mock("@/lib/api/applications", () => ({
  getApplication: (...args: unknown[]) => getApplicationMock(...args),
}));
vi.mock("@/lib/api/tracking", () => ({
  getTimeline: (...args: unknown[]) => getTimelineMock(...args),
  changeStatus: (...args: unknown[]) => changeStatusMock(...args),
  updateTracking: (...args: unknown[]) => updateTrackingMock(...args),
}));

function appliedApplication(status: ApplicationStatus = "APPLIED", extra = {}) {
  return makeApplication({
    status,
    tracking: makeTracking({ status, ...extra }),
  });
}

const HISTORY: TimelineEntry[] = [
  makeTimelineEntry({
    id: "e4",
    kind: "NOTE",
    occurred_at: "2026-10-09T08:00:00Z",
    previous_status: "APPLIED",
    status: "APPLIED",
    note: "Recruiter called today.\nBring ID & <b>passport</b>.",
  }),
  makeTimelineEntry({
    id: "e3",
    kind: "SUBMISSION",
    occurred_at: "2026-10-07T09:00:09Z",
    previous_status: null,
    status: null,
    source: null,
    actor: null,
    submission: {
      attempt_id: "attempt-1",
      result: "SUCCESS",
      destination_domain: "boards.example.com",
      confirmation_reference: "ABC-123",
      confirmation_summary: "Thank you for applying",
      user_verified_outcome: null,
      user_verified_at: null,
      failure_code: null,
    },
  }),
  makeTimelineEntry({
    id: "e2",
    occurred_at: "2026-10-07T09:00:09Z",
    source: "SUBMISSION",
    actor: "SYSTEM",
    submission_attempt_id: "attempt-1",
  }),
  makeTimelineEntry({
    id: "e1",
    occurred_at: "2026-10-05T09:00:00Z",
    previous_status: "SAVED",
    status: "PREPARING",
    source: "SYSTEM",
    actor: "SYSTEM",
  }),
  makeTimelineEntry({
    id: "e0",
    occurred_at: "2026-10-04T09:00:00Z",
    previous_status: null,
    status: "SAVED",
    source: "SYSTEM",
    actor: "SYSTEM",
  }),
];

async function renderTracking(application = appliedApplication(), history = HISTORY) {
  getApplicationMock.mockResolvedValue(application);
  getTimelineMock.mockResolvedValue({ items: history });
  render(<ApplicationTracking applicationId={application.id} />);
  await screen.findByRole("heading", { level: 1 });
}

function response(status: ApplicationStatus, extra = {}, changed = true): StatusChangeResponse {
  return { changed, tracking: makeTracking({ status, ...extra }) };
}

describe("ApplicationTracking", () => {
  beforeEach(() => {
    for (const mock of [getApplicationMock, getTimelineMock, changeStatusMock, updateTrackingMock]) {
      mock.mockReset();
    }
  });

  describe("header and summary", () => {
    it("shows the company, position, status and a link to the original posting", async () => {
      await renderTracking();
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        "Senior Backend Engineer (Python)"
      );
      expect(screen.getByText(/Acme GmbH/)).toBeInTheDocument();
      expect(screen.getByLabelText("Application status")).toHaveTextContent("Applied");
      const link = screen.getByRole("link", { name: /Open original job posting/ });
      expect(link).toHaveAttribute(
        "href",
        "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x"
      );
      expect(link).toHaveAttribute("target", "_blank");
      expect(screen.getByRole("link", { name: "Open application workspace" })).toHaveAttribute(
        "href",
        "/applications/app-1"
      );
    });

    it("shows the application date, last update and follow-up date", async () => {
      await renderTracking(
        appliedApplication("FOLLOW_UP", { follow_up_at: "2026-10-21T09:00:00Z" })
      );
      const summary = screen.getByRole("heading", { name: "Summary" }).closest("div")!;
      expect(summary).toHaveTextContent("Application date");
      expect(summary).toHaveTextContent("7 Oct 2026");
      expect(summary).toHaveTextContent("Last updated");
      expect(summary).toHaveTextContent("Follow up on");
      expect(summary).toHaveTextContent("21 Oct 2026");
    });

    it("says an unsubmitted application has not been applied", async () => {
      await renderTracking(makeApplication(), HISTORY.slice(3));
      expect(screen.getByText("Not applied yet")).toBeInTheDocument();
    });

    it("shows the submission result, and never calls an uncertain one a success", async () => {
      await renderTracking(
        makeApplication({
          tracking: makeTracking({
            status: "PREPARING",
            submission: makeSubmissionSummary({
              result: "SUBMISSION_UNKNOWN",
              needs_verification: true,
              confirmation_reference: null,
            }),
          }),
        })
      );
      const summary = screen.getByRole("heading", { name: "Summary" }).closest("div")!;
      expect(summary).toHaveTextContent("Submission result uncertain — user verification required");
      expect(summary).not.toHaveTextContent("Submitted successfully");
      expect(screen.getByRole("status")).toHaveTextContent(/couldn't tell whether the submission went through/);
    });

    it("keeps working when the original posting has gone: it shows what the application recorded", async () => {
      const application = makeApplication({
        source_is_active: null,
        tracking: makeTracking({ status: "APPLIED" }),
      });
      await renderTracking(application);
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Senior Backend Engineer");
      expect(screen.getByRole("link", { name: /original job posting/i })).toBeInTheDocument();
    });
  });

  describe("timeline", () => {
    it("lists every event newest first, in plain words", async () => {
      await renderTracking();
      const items = within(screen.getByRole("list", { name: "Application timeline" })).getAllByRole("listitem");
      expect(items.map((item) => item.getAttribute("data-kind"))).toEqual([
        "NOTE",
        "SUBMISSION",
        "STATUS_CHANGE",
        "STATUS_CHANGE",
        "STATUS_CHANGE",
      ]);
      expect(items[1]).toHaveTextContent("Application submitted");
      expect(items[1]).toHaveTextContent("Result: SUCCESS. Confirmation: ABC-123");
      expect(items[2]).toHaveTextContent("Applied");
      expect(items[2]).toHaveTextContent("Application successfully submitted.");
      expect(items[3]).toHaveTextContent("Application workspace created.");
      expect(items[4]).toHaveTextContent("Saved");
    });

    it("shows the user's notes exactly as written, as text", async () => {
      await renderTracking();
      const note = screen.getByText(/Recruiter called today/);
      expect(note.textContent).toBe("Recruiter called today.\nBring ID & <b>passport</b>.");
      expect(note.querySelector("b")).toBeNull(); // markup stays text
    });

    it("shows an uncertain submission as needing verification", async () => {
      await renderTracking(makeApplication(), [
        makeTimelineEntry({
          kind: "SUBMISSION",
          status: null,
          previous_status: null,
          submission: {
            attempt_id: "a",
            result: "SUBMISSION_UNKNOWN",
            destination_domain: "boards.example.com",
            confirmation_reference: null,
            confirmation_summary: null,
            user_verified_outcome: null,
            user_verified_at: null,
            failure_code: "NO_CONFIRMATION",
          },
        }),
      ]);
      const timeline = screen.getByRole("list", { name: "Application timeline" });
      expect(timeline).toHaveTextContent("Submission result uncertain — user verification required.");
      expect(timeline).not.toHaveTextContent("SUCCESS");
    });

    it("shows a timeline refresh error after a change, without losing the new status", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      changeStatusMock.mockResolvedValue(response("INTERVIEW"));
      getTimelineMock.mockRejectedValue(new ApiError(500, "SERVER_ERROR", "x", {}));

      await user.selectOptions(screen.getByLabelText("New status"), "INTERVIEW");
      await user.click(screen.getByRole("button", { name: "Change status" }));

      expect(await screen.findByText("Unable to load the timeline. Please try again.")).toBeInTheDocument();
      expect(screen.getByLabelText("Application status")).toHaveTextContent("Interview");
    });

    it("has an empty state", async () => {
      await renderTracking(appliedApplication(), []);
      expect(screen.getByText("Nothing has happened yet.")).toBeInTheDocument();
    });
  });

  describe("loading and errors", () => {
    it("shows a loading state", () => {
      getApplicationMock.mockReturnValue(new Promise(() => {}));
      getTimelineMock.mockReturnValue(new Promise(() => {}));
      render(<ApplicationTracking applicationId="app-1" />);
      expect(screen.getByRole("status")).toHaveTextContent("Loading application...");
    });

    it("says another user's (or a missing) application isn't yours", async () => {
      getApplicationMock.mockRejectedValue(new ApiError(404, "NOT_FOUND", "nf", {}));
      getTimelineMock.mockRejectedValue(new ApiError(404, "NOT_FOUND", "nf", {}));
      render(<ApplicationTracking applicationId="someone-elses" />);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "This application doesn't exist or isn't yours."
      );
      expect(screen.getByRole("link", { name: "← My Applications" })).toHaveAttribute(
        "href",
        "/applications"
      );
    });

    it("shows a generic load error", async () => {
      getApplicationMock.mockRejectedValue(new ApiError(500, "SERVER_ERROR", "x", {}));
      getTimelineMock.mockRejectedValue(new ApiError(500, "SERVER_ERROR", "x", {}));
      render(<ApplicationTracking applicationId="app-1" />);
      expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load this application");
    });
  });

  describe("changing the status", () => {
    it("offers exactly the statuses the server allows", async () => {
      await renderTracking(appliedApplication("PREPARING"));
      const select = screen.getByLabelText("New status");
      expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
        "Choose a status",
        "Applied",
        "Withdrawn",
      ]);
    });

    it("sends only the status and the note, then shows the new status and reloads the timeline", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      changeStatusMock.mockResolvedValue(response("INTERVIEW"));
      getTimelineMock.mockResolvedValue({
        items: [
          makeTimelineEntry({
            id: "new",
            status: "INTERVIEW",
            previous_status: "APPLIED",
            note: "Interview invitation received by email.",
          }),
          ...HISTORY,
        ],
      });

      await user.selectOptions(screen.getByLabelText("New status"), "INTERVIEW");
      await user.type(screen.getByLabelText("Note (optional)"), "Interview invitation received by email.");
      await user.click(screen.getByRole("button", { name: "Change status" }));

      expect(changeStatusMock).toHaveBeenCalledTimes(1);
      const [applicationId, body] = changeStatusMock.mock.calls[0] as [string, StatusChangeRequest];
      expect(applicationId).toBe("app-1");
      expect(body).toEqual({ status: "INTERVIEW", note: "Interview invitation received by email." });
      expect(await screen.findByText("Status updated.")).toBeInTheDocument();
      await waitFor(() =>
        expect(screen.getByLabelText("Application status")).toHaveTextContent("Interview")
      );
      expect(await screen.findByText("Interview invitation received by email.")).toBeInTheDocument();
      // the choices follow the server's new answer
      expect(within(screen.getByLabelText("New status")).queryByText("Interview")).not.toBeInTheDocument();
    });

    it("walks Applied -> Interview -> Offer", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));

      changeStatusMock.mockResolvedValueOnce(response("INTERVIEW"));
      await user.selectOptions(screen.getByLabelText("New status"), "INTERVIEW");
      await user.click(screen.getByRole("button", { name: "Change status" }));
      await waitFor(() => expect(screen.getByLabelText("Application status")).toHaveTextContent("Interview"));

      changeStatusMock.mockResolvedValueOnce(response("OFFER"));
      await user.selectOptions(screen.getByLabelText("New status"), "OFFER");
      await user.click(screen.getByRole("button", { name: "Change status" }));
      await waitFor(() => expect(screen.getByLabelText("Application status")).toHaveTextContent("Offer"));

      expect(changeStatusMock.mock.calls.map((call) => (call[1] as StatusChangeRequest).status)).toEqual([
        "INTERVIEW",
        "OFFER",
      ]);
      expect(getTimelineMock.mock.calls.length).toBeGreaterThanOrEqual(3); // load + one per change
    });

    it("does not send an empty note or a client-side fact", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      changeStatusMock.mockResolvedValue(response("REJECTED"));
      await user.selectOptions(screen.getByLabelText("New status"), "REJECTED");
      await user.type(screen.getByLabelText("Note (optional)"), "   ");
      await user.click(screen.getByRole("button", { name: "Change status" }));
      const body = changeStatusMock.mock.calls[0]![1] as Record<string, unknown>;
      expect(body).toEqual({ status: "REJECTED" });
      for (const forbidden of ["previous_status", "user_id", "changed_at", "actor", "source"]) {
        expect(body).not.toHaveProperty(forbidden);
      }
    });

    it("needs a status to be chosen first", async () => {
      await renderTracking(appliedApplication("APPLIED"));
      expect(screen.getByRole("button", { name: "Change status" })).toBeDisabled();
    });

    it("shows the server's refusal and keeps the status", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      changeStatusMock.mockRejectedValue(
        new ApiError(409, "INVALID_STATUS_TRANSITION", "An application can't go ...", {})
      );
      await user.selectOptions(screen.getByLabelText("New status"), "OFFER");
      await user.click(screen.getByRole("button", { name: "Change status" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(/isn't allowed from the current status/);
      expect(screen.getByLabelText("Application status")).toHaveTextContent("Applied");
    });

    it("tells the user when nothing changed", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      changeStatusMock.mockResolvedValue(response("APPLIED", {}, false));
      await user.selectOptions(screen.getByLabelText("New status"), "FOLLOW_UP");
      await user.click(screen.getByRole("button", { name: "Change status" }));
      expect(await screen.findByText("Nothing changed.")).toBeInTheDocument();
    });

    it("can set a follow-up date together with Follow-up", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      changeStatusMock.mockResolvedValue(response("FOLLOW_UP"));
      await user.selectOptions(screen.getByLabelText("New status"), "FOLLOW_UP");
      await user.type(screen.getByLabelText("Follow up on (optional)"), "2026-10-21T09:00");
      await user.click(screen.getByRole("button", { name: "Change status" }));
      const body = changeStatusMock.mock.calls[0]![1] as StatusChangeRequest;
      expect(body.status).toBe("FOLLOW_UP");
      expect(body.follow_up_at).toBe(new Date(2026, 9, 21, 9, 0).toISOString());
    });

    it("offers no follow-up date for a status that doesn't wait", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      await user.selectOptions(screen.getByLabelText("New status"), "REJECTED");
      expect(screen.queryByLabelText("Follow up on (optional)")).not.toBeInTheDocument();
    });

    it("says a closed application can only be corrected", async () => {
      await renderTracking(appliedApplication("REJECTED"));
      expect(screen.getByText(/This application is closed/)).toBeInTheDocument();
      expect(screen.queryByLabelText("New status")).not.toBeInTheDocument();
    });
  });

  describe("correcting the status", () => {
    it("needs a status and a note, and is sent as an explicit correction", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("NO_RESPONSE"));
      const form = screen.getByRole("form", { name: "Correct the status" });
      const submit = within(form).getByRole("button", { name: "Record correction" });
      expect(submit).toBeDisabled();

      changeStatusMock.mockResolvedValue(response("INTERVIEW"));
      await user.selectOptions(within(form).getByLabelText("Correct status to"), "INTERVIEW");
      expect(submit).toBeDisabled(); // a note is required
      await user.type(within(form).getByLabelText("Why (required)"), "They replied after all.");
      await user.click(submit);

      expect(changeStatusMock).toHaveBeenCalledWith("app-1", {
        status: "INTERVIEW",
        note: "They replied after all.",
        correction: true,
      });
      expect(await screen.findByText("Correction recorded.")).toBeInTheDocument();
    });

    it("can go to any other status but not the current one", async () => {
      await renderTracking(appliedApplication("APPLIED"));
      const options = within(screen.getByLabelText("Correct status to")).getAllByRole("option");
      const labels = options.map((o) => o.textContent);
      expect(labels).toContain("Saved");
      expect(labels).toContain("No response");
      expect(labels).not.toContain("Applied");
    });
  });

  describe("follow-up date", () => {
    it("saves a date, with a note that nothing is sent", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      expect(screen.getByText(/sends nothing and does nothing when this date passes/)).toBeInTheDocument();

      updateTrackingMock.mockResolvedValue(response("APPLIED", { follow_up_at: "2026-10-21T07:00:00Z" }));
      await user.type(screen.getByLabelText("Follow up on"), "2026-10-21T09:00");
      await user.click(screen.getByRole("button", { name: "Save date" }));

      expect(updateTrackingMock).toHaveBeenCalledWith("app-1", {
        follow_up_at: new Date(2026, 9, 21, 9, 0).toISOString(),
      });
      expect(await screen.findByText("Follow-up date saved.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Clear date" })).toBeInTheDocument();
    });

    it("clears a date", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("FOLLOW_UP", { follow_up_at: "2026-10-21T09:00:00Z" }));
      updateTrackingMock.mockResolvedValue(response("FOLLOW_UP", { follow_up_at: null }));
      await user.click(screen.getByRole("button", { name: "Clear date" }));
      expect(updateTrackingMock).toHaveBeenCalledWith("app-1", { follow_up_at: null });
      expect(await screen.findByText("Follow-up date cleared.")).toBeInTheDocument();
    });

    it("is not offered while the application isn't waiting on anyone", async () => {
      await renderTracking(appliedApplication("PREPARING"));
      expect(screen.queryByRole("form", { name: "Follow-up date" })).not.toBeInTheDocument();
      expect(screen.getByText(/while the application is applied, in follow-up or at interview/)).toBeInTheDocument();
    });
  });

  describe("notes", () => {
    it("adds a note exactly as written", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      updateTrackingMock.mockResolvedValue(response("APPLIED"));
      const form = screen.getByRole("form", { name: "Add a note" });
      expect(within(form).getByRole("button", { name: "Add note" })).toBeDisabled();

      await user.type(within(form).getByLabelText("Note"), "Interview scheduled for 14 October.");
      await user.click(within(form).getByRole("button", { name: "Add note" }));

      expect(updateTrackingMock).toHaveBeenCalledWith("app-1", {
        note: "Interview scheduled for 14 October.",
      });
      expect(await screen.findByText("Note added.")).toBeInTheDocument();
      expect(within(form).getByLabelText("Note")).toHaveValue(""); // cleared for the next one
    });

    it("shows an error when saving fails and keeps what was typed", async () => {
      const user = userEvent.setup();
      await renderTracking(appliedApplication("APPLIED"));
      updateTrackingMock.mockRejectedValue(new Error("network"));
      const form = screen.getByRole("form", { name: "Add a note" });
      await user.type(within(form).getByLabelText("Note"), "Keep me");
      await user.click(within(form).getByRole("button", { name: "Add note" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Unable to save. Please try again.");
      expect(within(form).getByLabelText("Note")).toHaveValue("Keep me");
    });
  });
});
