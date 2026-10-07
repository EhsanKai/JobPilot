import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResumeReview } from "@/components/resume/ResumeReview";
import { ApiError } from "@/lib/http";
import type { ResumeProfileData, ReviewStatus } from "@/types/resumeProfile";

import { makeIntelligence, makeProfile, makeProfileData } from "./fixtures/resumeProfile";

const getResumeIntelligenceMock = vi.fn();
const getResumeSourceMock = vi.fn();
const reviewResumeItemMock = vi.fn();
const acceptPendingItemsMock = vi.fn();
const confirmResumeProfileMock = vi.fn();
const reopenResumeProfileMock = vi.fn();

vi.mock("@/lib/api/resumes", () => ({
  getResumeIntelligence: (...args: unknown[]) => getResumeIntelligenceMock(...args),
  getResumeSource: (...args: unknown[]) => getResumeSourceMock(...args),
  reviewResumeItem: (...args: unknown[]) => reviewResumeItemMock(...args),
  acceptPendingItems: (...args: unknown[]) => acceptPendingItemsMock(...args),
  confirmResumeProfile: (...args: unknown[]) => confirmResumeProfileMock(...args),
  reopenResumeProfile: (...args: unknown[]) => reopenResumeProfileMock(...args),
}));

function withStatus(data: ResumeProfileData, status: ReviewStatus): ResumeProfileData {
  const set = <T extends { review_status: ReviewStatus }>(items: T[]) =>
    items.map((item) => ({ ...item, review_status: status }));
  return {
    ...data,
    personal_information: set(data.personal_information),
    work_experience: set(data.work_experience),
    skills: set(data.skills),
    languages: set(data.languages),
  };
}

function item(id: string) {
  return screen.getByTestId(`review-item-${id}`);
}

describe("ResumeReview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getResumeIntelligenceMock.mockResolvedValue(makeIntelligence());
  });

  it("summarizes what was detected and organizes items into sections", async () => {
    render(<ResumeReview resumeId="resume-1" />);

    expect(await screen.findByText("3 skills detected")).toBeInTheDocument();
    expect(screen.getByText("1 work experience detected")).toBeInTheDocument();
    expect(screen.getByText(/Please review the extracted information/)).toBeInTheDocument();
    for (const heading of ["Personal Information", "Work Experience", "Skills", "Languages"]) {
      expect(screen.getByRole("heading", { name: new RegExp(heading) })).toBeInTheDocument();
    }
    // Empty sections are not rendered.
    expect(screen.queryByRole("heading", { name: /Certifications/ })).not.toBeInTheDocument();
    expect(screen.getByText("SAP IS-U Developer — Example Energy GmbH")).toBeInTheDocument();
  });

  it("shows confidence subtly for each item", async () => {
    render(<ResumeReview resumeId="resume-1" />);
    await screen.findByText("3 skills detected");

    expect(within(item("s-isu")).getByText("High confidence")).toBeInTheDocument();
    expect(within(item("s-btp")).getByText("Medium confidence")).toBeInTheDocument();
    expect(within(item("s-pm")).getByText("Low confidence")).toBeInTheDocument();
    expect(within(item("s-pm")).getByText("Inferred")).toBeInTheDocument();
  });

  it("shows evidence and the surrounding source text on request", async () => {
    getResumeSourceMock.mockResolvedValue({
      profile_id: "profile-1",
      extraction_method: "pypdf",
      page_count: 1,
      text: "EXPERIENCE\nWorked with SAP BTP for side-by-side extensions\nSKILLS",
    });
    const user = userEvent.setup();
    render(<ResumeReview resumeId="resume-1" />);
    await screen.findByText("3 skills detected");

    const btp = item("s-btp");
    await user.click(within(btp).getByRole("button", { name: "Why?" }));
    expect(
      within(btp).getByText("“Worked with SAP BTP for side-by-side extensions”")
    ).toBeInTheDocument();
    expect(within(btp).getByText("✓ Found in your resume")).toBeInTheDocument();

    await user.click(within(btp).getByRole("button", { name: "View source" }));
    const context = await within(btp).findByTestId("source-context");
    expect(context).toHaveTextContent("EXPERIENCE");
    expect(within(context).getByText("Worked with SAP BTP for side-by-side extensions").tagName).toBe(
      "MARK"
    );
    expect(getResumeSourceMock).toHaveBeenCalledWith("resume-1");

    // Unverified evidence is flagged.
    const pm = item("s-pm");
    await user.click(within(pm).getByRole("button", { name: "Why?" }));
    expect(within(pm).getByText(/Not found word-for-word/)).toBeInTheDocument();
  });

  it("accepts an item", async () => {
    const data = makeProfileData();
    data.skills[0] = { ...data.skills[0]!, review_status: "accepted" };
    reviewResumeItemMock.mockResolvedValue(makeProfile({ data }));
    const user = userEvent.setup();
    render(<ResumeReview resumeId="resume-1" />);
    await screen.findByText("3 skills detected");

    await user.click(within(item("s-isu")).getByRole("button", { name: "Accept" }));

    expect(reviewResumeItemMock).toHaveBeenCalledWith("resume-1", "s-isu", {
      action: "accept",
      changes: undefined,
    });
    expect(await within(item("s-isu")).findByText("Accepted")).toBeInTheDocument();
  });

  it("rejects an item", async () => {
    const data = makeProfileData();
    data.skills[2] = { ...data.skills[2]!, review_status: "rejected" };
    reviewResumeItemMock.mockResolvedValue(makeProfile({ data }));
    const user = userEvent.setup();
    render(<ResumeReview resumeId="resume-1" />);
    await screen.findByText("3 skills detected");

    await user.click(within(item("s-pm")).getByRole("button", { name: "Reject" }));

    expect(reviewResumeItemMock).toHaveBeenCalledWith("resume-1", "s-pm", {
      action: "reject",
      changes: undefined,
    });
    expect(await within(item("s-pm")).findByText("Rejected")).toBeInTheDocument();
    expect(within(item("s-pm")).getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("edits an item and only sends the changed fields", async () => {
    const data = makeProfileData();
    data.work_experience[0] = {
      ...data.work_experience[0]!,
      job_title: "SAP IS-U Consultant",
      review_status: "edited",
      user_edited_fields: ["job_title"],
    };
    reviewResumeItemMock.mockResolvedValue(makeProfile({ data }));
    const user = userEvent.setup();
    render(<ResumeReview resumeId="resume-1" />);
    await screen.findByText("3 skills detected");

    const role = item("w-1");
    await user.click(within(role).getByRole("button", { name: "Edit" }));
    const title = within(role).getByLabelText("Job title");
    expect(title).toHaveValue("SAP IS-U Developer");
    await user.clear(title);
    await user.type(title, "SAP IS-U Consultant");
    await user.click(within(role).getByRole("button", { name: "Save changes" }));

    expect(reviewResumeItemMock).toHaveBeenCalledWith("resume-1", "w-1", {
      action: "edit",
      changes: { job_title: "SAP IS-U Consultant" },
    });
    expect(await within(item("w-1")).findByText("Edited by you")).toBeInTheDocument();
    expect(within(item("w-1")).getByText("SAP IS-U Consultant — Example Energy GmbH")).toBeInTheDocument();
  });

  it("keeps the editor open and shows the error when an edit is invalid", async () => {
    reviewResumeItemMock.mockRejectedValue(
      new ApiError(422, "VALIDATION_ERROR", "'start_date' must be a year (YYYY) or year and month (YYYY-MM).", {})
    );
    const user = userEvent.setup();
    render(<ResumeReview resumeId="resume-1" />);
    await screen.findByText("3 skills detected");

    const role = item("w-1");
    await user.click(within(role).getByRole("button", { name: "Edit" }));
    const start = within(role).getByLabelText("Start date");
    await user.clear(start);
    await user.type(start, "last spring");
    await user.click(within(role).getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("must be a year");
    expect(within(item("w-1")).getByRole("button", { name: "Save changes" })).toBeInTheDocument();
  });

  it("only allows confirming once every item is reviewed", async () => {
    const reviewed = makeProfile({ data: withStatus(makeProfileData(), "accepted") });
    acceptPendingItemsMock.mockResolvedValue(reviewed);
    confirmResumeProfileMock.mockResolvedValue(
      makeProfile({
        data: reviewed.data,
        status: "confirmed",
        confirmed_at: "2026-01-02T00:00:00Z",
      })
    );
    const user = userEvent.setup();
    render(<ResumeReview resumeId="resume-1" />);
    await screen.findByText("3 skills detected");

    const confirm = screen.getByRole("button", { name: "Confirm Resume Profile" });
    expect(confirm).toBeDisabled();
    expect(screen.getByText("6 items still need review.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Accept all remaining" }));
    expect(acceptPendingItemsMock).toHaveBeenCalledWith("resume-1");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Confirm Resume Profile" })).toBeEnabled()
    );

    await user.click(screen.getByRole("button", { name: "Confirm Resume Profile" }));
    expect(confirmResumeProfileMock).toHaveBeenCalledWith("resume-1");
    expect(await screen.findByText("Confirmed")).toBeInTheDocument();
    expect(screen.getByText(/trusted Resume Profile/)).toBeInTheDocument();
    // A confirmed profile is read-only until reopened.
    expect(within(item("s-isu")).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit again" })).toBeInTheDocument();
  });

  it("reopens a confirmed profile for changes", async () => {
    const data = withStatus(makeProfileData(), "accepted");
    getResumeIntelligenceMock.mockResolvedValue(
      makeIntelligence({ profile: makeProfile({ data, status: "confirmed" }) })
    );
    reopenResumeProfileMock.mockResolvedValue(makeProfile({ data, status: "needs_review" }));
    const user = userEvent.setup();
    render(<ResumeReview resumeId="resume-1" />);

    await user.click(await screen.findByRole("button", { name: "Edit again" }));

    expect(reopenResumeProfileMock).toHaveBeenCalledWith("resume-1");
    expect(await screen.findByRole("button", { name: "Confirm Resume Profile" })).toBeEnabled();
  });

  it("shows conflicts with the user's profile without resolving them", async () => {
    getResumeIntelligenceMock.mockResolvedValue(
      makeIntelligence({
        profile: makeProfile({
          conflicts: [
            {
              field: "years_of_experience",
              label: "Years of experience",
              profile_value: "6",
              resume_value: "5",
              potential_conflict: true,
              message: "Different",
            },
          ],
        }),
      })
    );
    render(<ResumeReview resumeId="resume-1" />);

    const conflicts = await screen.findByTestId("conflicts");
    expect(conflicts).toHaveTextContent("Differences from your profile (1)");
    expect(conflicts).toHaveTextContent(
      "Years of experience: your profile says “6”, your resume says “5”."
    );
    expect(conflicts).toHaveTextContent("Neither value was changed.");
  });

  it("shows an empty state when the resume hasn't been analyzed", async () => {
    getResumeIntelligenceMock.mockResolvedValue(makeIntelligence({ profile: null }));
    render(<ResumeReview resumeId="resume-1" />);
    expect(await screen.findByText(/hasn't been analyzed yet/)).toBeInTheDocument();
  });
});
