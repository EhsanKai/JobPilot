import { describe, expect, it } from "vitest";

import { applicationListPath } from "@/lib/api/tracking";
import {
  ALL_STATUSES,
  STATUS_PRESENTATION,
  describeTimelineEntry,
  fromDateTimeLocal,
  statusLabel,
  submissionText,
  summaryRows,
  toDateTimeLocal,
  trackingErrorMessage,
} from "@/lib/tracking";
import { ApiError } from "@/lib/http";
import type { StatusCounts } from "@/types/application";

import { makeSubmissionSummary, makeTimelineEntry } from "./fixtures/application";

describe("status presentation", () => {
  it("has a distinct label and a symbol for every status", () => {
    expect(ALL_STATUSES).toEqual([
      "SAVED",
      "PREPARING",
      "APPLIED",
      "FOLLOW_UP",
      "INTERVIEW",
      "OFFER",
      "REJECTED",
      "WITHDRAWN",
      "NO_RESPONSE",
    ]);
    const labels = ALL_STATUSES.map(statusLabel);
    expect(new Set(labels).size).toBe(ALL_STATUSES.length);
    for (const status of ALL_STATUSES) {
      expect(STATUS_PRESENTATION[status].icon).not.toBe("");
      expect(STATUS_PRESENTATION[status].description).not.toBe("");
    }
  });

  it("keeps rejected, withdrawn and no response visually and textually distinct", () => {
    const { REJECTED, WITHDRAWN, NO_RESPONSE, INTERVIEW, OFFER, APPLIED } = STATUS_PRESENTATION;
    expect(new Set([REJECTED.tone, WITHDRAWN.tone, NO_RESPONSE.tone]).size).toBe(3);
    expect(new Set([REJECTED.icon, WITHDRAWN.icon, NO_RESPONSE.icon]).size).toBe(3);
    expect(statusLabel("REJECTED")).not.toBe(statusLabel("NO_RESPONSE"));
    expect(INTERVIEW.tone).toBe("positive");
    expect(OFFER.tone).toBe("positive");
    expect(APPLIED.tone).toBe("active");
  });
});

describe("applicationListPath", () => {
  it("asks for the first page with no filters by default", () => {
    expect(applicationListPath()).toBe("/applications?page=1&page_size=20");
  });

  it("sends every filter the user set and leaves the empty ones out", () => {
    const path = applicationListPath({
      page: 2,
      pageSize: 10,
      statuses: ["APPLIED", "INTERVIEW"],
      company: "  Acme  ",
      source: "",
      appliedFrom: "2026-03-01",
      appliedTo: "2026-03-31",
      sort: "applied",
    });
    const params = new URL(`http://x${path}`).searchParams;
    expect(params.getAll("status")).toEqual(["APPLIED", "INTERVIEW"]);
    expect(params.get("company")).toBe("Acme");
    expect(params.has("source")).toBe(false);
    expect(params.get("applied_from")).toBe("2026-03-01");
    expect(params.get("applied_to")).toBe("2026-03-31");
    expect(params.get("sort")).toBe("applied");
    expect(params.get("page")).toBe("2");
    expect(params.get("page_size")).toBe("10");
  });

  it("does not send the default sort", () => {
    expect(applicationListPath({ sort: "updated" })).not.toContain("sort=");
  });
});

describe("submissionText", () => {
  it("says nothing without a submission", () => {
    expect(submissionText(null)).toBeNull();
  });

  it("reports a confirmed submission with its reference", () => {
    expect(submissionText(makeSubmissionSummary())).toBe(
      "Submitted successfully · confirmation ABC-123"
    );
  });

  it("never calls an uncertain result a success", () => {
    const text = submissionText(
      makeSubmissionSummary({ result: "SUBMISSION_UNKNOWN", needs_verification: true })
    );
    expect(text).toBe("Submission result uncertain — user verification required");
    expect(text).not.toMatch(/successfully/i);
  });

  it("keeps the uncertain result visible after the user has checked", () => {
    expect(
      submissionText(
        makeSubmissionSummary({ result: "SUBMISSION_UNKNOWN", user_verified_outcome: "SUBMITTED" })
      )
    ).toContain("uncertain");
  });

  it("says a failed attempt did not submit", () => {
    expect(submissionText(makeSubmissionSummary({ result: "FAILED_BEFORE_SUBMIT" }))).toMatch(
      /Not submitted/
    );
  });
});

describe("describeTimelineEntry", () => {
  it("describes a user status change with both statuses", () => {
    const line = describeTimelineEntry(makeTimelineEntry({ status: "INTERVIEW", previous_status: "APPLIED" }));
    expect(line.title).toBe("Interview");
    expect(line.detail).toBe("You changed the status from Applied to Interview.");
    expect(line.note).toBeNull();
  });

  it("describes the system's first entries", () => {
    const saved = describeTimelineEntry(
      makeTimelineEntry({ status: "SAVED", previous_status: null, source: "SYSTEM", actor: "SYSTEM" })
    );
    const preparing = describeTimelineEntry(
      makeTimelineEntry({ status: "PREPARING", previous_status: "SAVED", source: "SYSTEM", actor: "SYSTEM" })
    );
    expect(saved.detail).toBe("Job saved.");
    expect(preparing.detail).toBe("Application workspace created.");
  });

  it("describes a submission-caused status change", () => {
    const system = describeTimelineEntry(
      makeTimelineEntry({ source: "SUBMISSION", actor: "SYSTEM" })
    );
    const user = describeTimelineEntry(makeTimelineEntry({ source: "SUBMISSION", actor: "USER" }));
    expect(system.title).toBe("Applied");
    expect(system.detail).toBe("Application successfully submitted.");
    expect(user.detail).toBe("You confirmed the application was submitted.");
  });

  it("passes the user's note through untouched", () => {
    const note = "  Recruiter called today.\n\nBring <b>ID</b> & passport  ";
    expect(describeTimelineEntry(makeTimelineEntry({ note })).note).toBe(note);
    expect(
      describeTimelineEntry(makeTimelineEntry({ kind: "NOTE", status: "APPLIED", previous_status: "APPLIED", note }))
        .note
    ).toBe(note);
  });

  it("marks a correction as a correction", () => {
    const line = describeTimelineEntry(
      makeTimelineEntry({ kind: "CORRECTION", status: "INTERVIEW", previous_status: "NO_RESPONSE", note: "They replied." })
    );
    expect(line.title).toBe("Corrected to Interview");
    expect(line.detail).toBe("You corrected the status from No response.");
  });

  it("shows a submission with its confirmation, and an unknown one as needing verification", () => {
    const success = describeTimelineEntry(
      makeTimelineEntry({
        kind: "SUBMISSION",
        status: null,
        previous_status: null,
        submission: {
          attempt_id: "a",
          result: "SUCCESS",
          destination_domain: "example.com",
          confirmation_reference: "ABC-123",
          confirmation_summary: "Thank you",
          user_verified_outcome: null,
          user_verified_at: null,
          failure_code: null,
        },
      })
    );
    expect(success.title).toBe("Application submitted");
    expect(success.detail).toBe("Result: SUCCESS. Confirmation: ABC-123");

    const unknown = describeTimelineEntry(
      makeTimelineEntry({
        kind: "SUBMISSION",
        status: null,
        previous_status: null,
        submission: {
          attempt_id: "a",
          result: "SUBMISSION_UNKNOWN",
          destination_domain: "example.com",
          confirmation_reference: null,
          confirmation_summary: null,
          user_verified_outcome: null,
          user_verified_at: null,
          failure_code: "NO_CONFIRMATION",
        },
      })
    );
    expect(unknown.detail).toBe("Submission result uncertain — user verification required.");
    expect(unknown.tone).toBe("warning");
  });
});

describe("date helpers", () => {
  it("round-trips a follow-up date through the datetime-local control", () => {
    const iso = new Date(2026, 9, 21, 9, 0).toISOString();
    expect(toDateTimeLocal(iso)).toBe("2026-10-21T09:00");
    expect(fromDateTimeLocal("2026-10-21T09:00")).toBe(iso);
  });

  it("treats empty or invalid values as no date", () => {
    expect(toDateTimeLocal(null)).toBe("");
    expect(toDateTimeLocal("nonsense")).toBe("");
    expect(fromDateTimeLocal("")).toBeNull();
    expect(fromDateTimeLocal("nonsense")).toBeNull();
  });
});

describe("summaryRows", () => {
  const counts = (partial: Partial<StatusCounts["counts"]>): StatusCounts => ({
    counts: {
      SAVED: 0,
      PREPARING: 0,
      APPLIED: 0,
      FOLLOW_UP: 0,
      INTERVIEW: 0,
      OFFER: 0,
      REJECTED: 0,
      WITHDRAWN: 0,
      NO_RESPONSE: 0,
      ...partial,
    },
    total: 0,
  });

  it("lists only the statuses that have applications, in the summary order", () => {
    expect(
      summaryRows(counts({ NO_RESPONSE: 2, APPLIED: 12, REJECTED: 5, INTERVIEW: 3, OFFER: 1, PREPARING: 4 }))
    ).toEqual([
      { status: "APPLIED", label: "Applied", count: 12 },
      { status: "INTERVIEW", label: "Interview", count: 3 },
      { status: "OFFER", label: "Offer", count: 1 },
      { status: "REJECTED", label: "Rejected", count: 5 },
      { status: "NO_RESPONSE", label: "No response", count: 2 },
    ]);
  });
});

describe("trackingErrorMessage", () => {
  it("explains a refused transition and points to the correction", () => {
    const error = new ApiError(409, "INVALID_STATUS_TRANSITION", "no", {});
    expect(trackingErrorMessage(error, "x")).toMatch(/isn't allowed/);
    expect(trackingErrorMessage(error, "x")).toMatch(/correct/i);
  });

  it("treats a missing application as not yours", () => {
    expect(trackingErrorMessage(new ApiError(404, "NOT_FOUND", "x", {}), "y")).toMatch(/isn't yours/);
  });

  it("uses the fallback for anything else", () => {
    expect(trackingErrorMessage(new Error("boom"), "Try again")).toBe("Try again");
  });
});
