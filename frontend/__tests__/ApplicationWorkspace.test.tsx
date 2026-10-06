import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationWorkspace } from "@/components/applications/ApplicationWorkspace";
import { ApiError } from "@/lib/http";

import {
  makeApplication,
  makeApplicationResume,
  makeContext,
  makePreparedApplication,
  makeProgress,
  makeResume,
} from "./fixtures/application";
import { makeMatchSummary } from "./fixtures/match";

const getApplicationMock = vi.fn();
const updateApplicationMock = vi.fn();
const listResumesMock = vi.fn();

vi.mock("@/lib/api/applications", () => ({
  getApplication: (...args: unknown[]) => getApplicationMock(...args),
  updateApplication: (...args: unknown[]) => updateApplicationMock(...args),
}));
vi.mock("@/lib/api/resumes", () => ({
  listResumes: (...args: unknown[]) => listResumesMock(...args),
}));

async function renderWorkspace(application = makeApplication()) {
  getApplicationMock.mockResolvedValue(application);
  render(<ApplicationWorkspace applicationId={application.id} />);
  await screen.findByRole("heading", { level: 1 });
}

function section(name: string) {
  return screen.getByRole("heading", { name }).closest("div")!;
}

describe("ApplicationWorkspace", () => {
  beforeEach(() => {
    getApplicationMock.mockReset();
    updateApplicationMock.mockReset();
    listResumesMock.mockReset();
    listResumesMock.mockResolvedValue([makeResume()]);
  });

  describe("loading and loading failures", () => {
    it("shows a loading state, then the workspace", async () => {
      getApplicationMock.mockResolvedValue(makeApplication());
      render(<ApplicationWorkspace applicationId="app-1" />);

      expect(screen.getByText("Loading application...")).toBeInTheDocument();
      expect(
        await screen.findByRole("heading", { level: 1, name: "Senior Backend Engineer (Python)" })
      ).toBeInTheDocument();
      expect(getApplicationMock).toHaveBeenCalledWith("app-1");
      expect(screen.queryByText("Loading application...")).not.toBeInTheDocument();
    });

    it("says an application that doesn't exist, or isn't yours, isn't there", async () => {
      getApplicationMock.mockRejectedValue(new ApiError(404, "NOT_FOUND", "Application not found.", {}));
      render(<ApplicationWorkspace applicationId="missing" />);

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "This application doesn't exist or isn't yours."
      );
      expect(screen.getByRole("link", { name: "← Back to jobs" })).toHaveAttribute("href", "/jobs");
      expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    });

    it("shows a generic error for other failures", async () => {
      getApplicationMock.mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "boom", {}));
      render(<ApplicationWorkspace applicationId="app-1" />);

      expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load this application");
    });

    it("still works when the resume list can't be loaded", async () => {
      listResumesMock.mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "boom", {}));
      await renderWorkspace();

      expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
      expect(screen.getByText(/Version 2 · cv-2026\.pdf/)).toBeInTheDocument();
    });
  });

  describe("the job", () => {
    it("shows the job as it was when the application started", async () => {
      await renderWorkspace();

      const job = section("Job");
      expect(within(job).getByText("Senior Backend Engineer (Python)")).toBeInTheDocument();
      expect(within(job).getByText("Acme GmbH")).toBeInTheDocument();
      expect(within(job).getByText("Berlin, Germany")).toBeInTheDocument();
      expect(within(job).getByText("Greenhouse (acme)")).toBeInTheDocument();
      expect(within(job).getByText("€70,000 – €90,000 per year")).toBeInTheDocument();
      expect(within(job).getByText("Hybrid")).toBeInTheDocument();
      expect(within(job).getByText(/As it was when you started this application/)).toBeInTheDocument();
      expect(within(job).getByRole("link", { name: "see the current job" })).toHaveAttribute(
        "href",
        "/jobs/job-1"
      );
    });

    it("links to the original posting in a new tab, exactly as the source gave it", async () => {
      await renderWorkspace();

      const link = screen.getByRole("link", { name: "View original posting ↗" });
      expect(link).toHaveAttribute(
        "href",
        "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x"
      );
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      expect(screen.getByRole("link", { name: "← Back to job" })).toHaveAttribute("href", "/jobs/job-1");
    });

    it("offers the application page only when it differs from the posting", async () => {
      await renderWorkspace();
      expect(screen.queryByRole("link", { name: "Application page ↗" })).not.toBeInTheDocument();
    });

    it("shows where applying actually happens when the source says so", async () => {
      const context = makeContext();
      context.source.application_url = "https://jobs.lever.co/acme/abc/apply";
      await renderWorkspace(makeApplication({ context }));

      expect(screen.getByRole("link", { name: "Application page ↗" })).toHaveAttribute(
        "href",
        "https://jobs.lever.co/acme/abc/apply"
      );
    });

    it("never renders unsafe links", async () => {
      const context = makeContext();
      context.source.original_url = "javascript:alert(1)";
      context.source.application_url = "javascript:alert(2)";
      await renderWorkspace(makeApplication({ context }));

      for (const link of screen.queryAllByRole("link")) {
        expect(link.getAttribute("href")).not.toMatch(/^javascript:/);
      }
      expect(screen.queryByRole("link", { name: "View original posting ↗" })).not.toBeInTheDocument();
      expect(screen.getByText("The source gave no link to the posting.")).toBeInTheDocument();
    });

    it("warns when the source no longer lists the posting", async () => {
      await renderWorkspace(makeApplication({ source_is_active: false }));
      expect(screen.getByText(/no longer listed by its source/)).toBeInTheDocument();
    });

    it("says nothing about the listing when the posting is still active or unknown", async () => {
      await renderWorkspace(makeApplication({ source_is_active: true }));
      expect(screen.queryByText(/no longer listed by its source/)).not.toBeInTheDocument();
    });

    it("marks what the posting doesn't state", async () => {
      const context = makeContext();
      context.job.salary_min = null;
      context.job.salary_max = null;
      context.job.work_model = null;
      context.job.location = null;
      await renderWorkspace(makeApplication({ context }));

      expect(within(section("Job")).getAllByText("Not stated").length).toBeGreaterThanOrEqual(3);
    });

    it("makes clear that nothing is submitted", async () => {
      await renderWorkspace();
      expect(screen.getByText(/Starting an application does not submit it/)).toBeInTheDocument();
      expect(screen.getByLabelText("Application status")).toHaveTextContent("In preparation");
    });
  });

  describe("empty (default) preparation fields", () => {
    it("starts with nothing chosen for the user", async () => {
      await renderWorkspace();

      expect(screen.getByLabelText("Salary expectation")).toHaveValue(null);
      expect(screen.getByLabelText("Currency")).toHaveValue("");
      expect(screen.getByLabelText(/Mention salary/)).toHaveValue("");
      expect(screen.getByRole("option", { name: "Not decided yet", selected: true })).toBeInTheDocument();
      expect(screen.getByLabelText("Available from")).toHaveValue("");
      expect(screen.getByLabelText("Notice period")).toHaveValue(null);
      expect(screen.getByLabelText("Application language")).toHaveValue("");
      expect(
        screen.getByRole("option", { name: "Select a language", selected: true })
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Work authorization (optional)")).toHaveValue("");
      expect(screen.getByLabelText("Private notes")).toHaveValue("");
    });

    it("offers German and English, and leaves the choice to the user", async () => {
      await renderWorkspace();
      const select = screen.getByLabelText("Application language");

      expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
        "Select a language",
        "German",
        "English",
      ]);
      expect(screen.getByText(/never guesses it from the job/)).toBeInTheDocument();
    });

    it("has nothing to save until something changes", async () => {
      await renderWorkspace();

      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
      expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
      await userEvent.type(screen.getByLabelText("Private notes"), "x");
      expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    });

    it("shows saved values", async () => {
      await renderWorkspace(makePreparedApplication());

      expect(screen.getByLabelText("Salary expectation")).toHaveValue(78000);
      expect(screen.getByLabelText("Currency")).toHaveValue("EUR");
      expect(screen.getByRole("option", { name: "Yes, mention it", selected: true })).toBeInTheDocument();
      expect(screen.getByLabelText("Available from")).toHaveValue("2027-01-01");
      expect(screen.getByLabelText("Notice period")).toHaveValue(3);
      expect(screen.getByLabelText("Notice period unit")).toHaveValue("months");
      expect(screen.getByRole("option", { name: "German", selected: true })).toBeInTheDocument();
      expect(
        screen.getByRole("option", { name: "I am authorized to work there", selected: true })
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Private notes")).toHaveValue("Referral from Alex.");
    });
  });

  describe("saving", () => {
    it("saves only what changed and shows the result", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockResolvedValue(makePreparedApplication({ work_authorization: null }));

      await user.type(screen.getByLabelText("Salary expectation"), "78000");
      await user.type(screen.getByLabelText("Currency"), "eur");
      await user.selectOptions(screen.getByLabelText(/Mention salary/), "yes");
      fireEvent.change(screen.getByLabelText("Available from"), { target: { value: "2027-01-01" } });
      await user.type(screen.getByLabelText("Notice period"), "3");
      await user.selectOptions(screen.getByLabelText("Application language"), "de");
      await user.type(screen.getByLabelText("Private notes"), "Referral from Alex.");
      expect(screen.getByLabelText("Currency")).toHaveValue("EUR");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => expect(updateApplicationMock).toHaveBeenCalledTimes(1));
      expect(updateApplicationMock).toHaveBeenCalledWith("app-1", {
        salary_expectation: "78000",
        salary_currency: "EUR",
        mention_salary_in_letter: true,
        available_from: "2027-01-01",
        notice_period: { value: 3, unit: "months" },
        application_language: "de",
        notes: "Referral from Alex.",
      });
      expect(await screen.findByText("Application saved.")).toBeInTheDocument();
      // The form now shows the saved state and has nothing left to save.
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
      expect(screen.getByLabelText("Salary expectation")).toHaveValue(78000);
    });

    it("updates the progress checklist from the saved application", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      expect(screen.getByLabelText("Progress summary")).toHaveTextContent("2 of 6 steps complete");
      updateApplicationMock.mockResolvedValue(makePreparedApplication());

      await user.selectOptions(screen.getByLabelText("Application language"), "de");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await screen.findByText("Application saved.");
      expect(screen.getByLabelText("Progress summary")).toHaveTextContent("6 of 6 steps complete");
    });

    it("saves a changed note on its own", async () => {
      const user = userEvent.setup();
      await renderWorkspace(makePreparedApplication());
      updateApplicationMock.mockResolvedValue(makePreparedApplication({ notes: "Ask about remote." }));

      const notes = screen.getByLabelText("Private notes");
      await user.clear(notes);
      await user.type(notes, "Ask about remote.");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await screen.findByText("Application saved.");
      expect(updateApplicationMock).toHaveBeenCalledWith("app-1", { notes: "Ask about remote." });
    });

    it("clears a value the user empties", async () => {
      const user = userEvent.setup();
      await renderWorkspace(makePreparedApplication());
      updateApplicationMock.mockResolvedValue(makePreparedApplication({ notice_period: null }));

      await user.clear(screen.getByLabelText("Notice period"));
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await screen.findByText("Application saved.");
      expect(updateApplicationMock).toHaveBeenCalledWith("app-1", { notice_period: null });
    });

    it("can record 'don't mention the salary' as a decision of its own", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockResolvedValue(makeApplication({ mention_salary_in_letter: false }));

      await user.selectOptions(screen.getByLabelText(/Mention salary/), "no");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await screen.findByText("Application saved.");
      expect(updateApplicationMock).toHaveBeenCalledWith("app-1", { mention_salary_in_letter: false });
    });

    it("shows a failed save, keeps what was typed, and lets the user retry", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockRejectedValueOnce(
        new ApiError(500, "INTERNAL_ERROR", "An unexpected error occurred. Please try again later.", {})
      );

      await user.type(screen.getByLabelText("Private notes"), "Keep me");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("An unexpected error occurred");
      expect(screen.queryByText("Application saved.")).not.toBeInTheDocument();
      expect(screen.getByLabelText("Private notes")).toHaveValue("Keep me");
      expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();

      updateApplicationMock.mockResolvedValueOnce(makeApplication({ notes: "Keep me" }));
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      expect(await screen.findByText("Application saved.")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("shows a network failure", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockRejectedValue(new TypeError("Failed to fetch"));

      await user.type(screen.getByLabelText("Private notes"), "x");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Unable to save your changes");
    });

    it("disables the button while saving, so a double click saves once", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      let finish: (value: unknown) => void = () => {};
      updateApplicationMock.mockReturnValue(new Promise((resolve) => (finish = resolve)));

      await user.type(screen.getByLabelText("Private notes"), "x");
      await user.dblClick(screen.getByRole("button", { name: "Save changes" }));

      expect(screen.getByRole("button", { name: "Saving..." })).toBeDisabled();
      finish(makeApplication({ notes: "x" }));
      await screen.findByText("Application saved.");
      expect(updateApplicationMock).toHaveBeenCalledTimes(1);
    });

    it("hides the 'saved' message again once the user edits something", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockResolvedValue(makeApplication({ notes: "x" }));
      await user.type(screen.getByLabelText("Private notes"), "x");
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      await screen.findByText("Application saved.");

      await user.type(screen.getByLabelText("Private notes"), "y");

      expect(screen.queryByText("Application saved.")).not.toBeInTheDocument();
    });
  });

  describe("validation", () => {
    it("rejects an invalid salary before anything is sent", async () => {
      const user = userEvent.setup();
      await renderWorkspace();

      await user.type(screen.getByLabelText("Salary expectation"), "0");
      await user.type(screen.getByLabelText("Currency"), "EUR");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByText("Enter an amount greater than 0.")).toBeInTheDocument();
      expect(screen.getByLabelText("Salary expectation")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByRole("alert")).toHaveTextContent("Please fix the highlighted fields.");
      expect(updateApplicationMock).not.toHaveBeenCalled();
    });

    it("asks for the currency that goes with a salary", async () => {
      const user = userEvent.setup();
      await renderWorkspace();

      await user.type(screen.getByLabelText("Salary expectation"), "78000");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByText("Enter the currency (for example EUR).")).toBeInTheDocument();
      expect(updateApplicationMock).not.toHaveBeenCalled();
    });

    it("rejects a malformed currency and a fractional notice period", async () => {
      const user = userEvent.setup();
      await renderWorkspace();

      await user.type(screen.getByLabelText("Currency"), "EU");
      await user.type(screen.getByLabelText("Notice period"), "1.5");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByText("Use a 3-letter currency code, for example EUR.")).toBeInTheDocument();
      expect(screen.getByText("Enter a whole number of 0 or more.")).toBeInTheDocument();
      expect(updateApplicationMock).not.toHaveBeenCalled();
    });

    it("moves keyboard focus to the first field with a problem", async () => {
      const user = userEvent.setup();
      await renderWorkspace();

      await user.type(screen.getByLabelText("Salary expectation"), "0");
      await user.type(screen.getByLabelText("Currency"), "EUR");
      await user.type(screen.getByLabelText("Notice period"), "1.5");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await screen.findByText("Enter an amount greater than 0.");
      expect(screen.getByLabelText("Salary expectation")).toHaveFocus();
    });

    it("moves focus to the select a server error names, and marks it invalid", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockRejectedValue(
        new ApiError(422, "VALIDATION_ERROR", "Application language must be one of: de, en.", {
          field: "application_language",
        })
      );

      await user.type(screen.getByLabelText("Private notes"), "x");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      const language = screen.getByLabelText("Application language");
      expect(await screen.findByText("Application language must be one of: de, en.")).toBeInTheDocument();
      expect(language).toHaveFocus();
      expect(language).toHaveAttribute("aria-invalid", "true");
      // The message is wired to the control for screen readers.
      const message = screen.getByText("Application language must be one of: de, en.");
      expect(language).toHaveAttribute("aria-describedby", message.id);
    });

    it("clears a field's error once the user edits it", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      await user.type(screen.getByLabelText("Currency"), "EU");
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      await screen.findByText("Use a 3-letter currency code, for example EUR.");

      await user.type(screen.getByLabelText("Currency"), "R");

      expect(screen.queryByText("Use a 3-letter currency code, for example EUR.")).not.toBeInTheDocument();
    });

    it("shows the server's validation message next to the field it concerns", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockRejectedValue(
        new ApiError(422, "VALIDATION_ERROR", "Available from must be a realistic date.", {
          errors: [
            {
              loc: ["body", "available_from"],
              msg: "Value error, Available from must be a realistic date.",
            },
          ],
        })
      );

      fireEvent.change(screen.getByLabelText("Available from"), { target: { value: "2999-01-01" } });
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByText("Available from must be a realistic date.")).toBeInTheDocument();
      expect(screen.getByLabelText("Available from")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByRole("alert")).toHaveTextContent("Please fix the highlighted fields.");
    });

    it("shows a server error that names no field as the general message", async () => {
      const user = userEvent.setup();
      await renderWorkspace();
      updateApplicationMock.mockRejectedValue(
        new ApiError(404, "NOT_FOUND", "Application not found.", {})
      );

      await user.type(screen.getByLabelText("Private notes"), "x");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Application not found.");
    });
  });

  describe("resume", () => {
    it("shows the selected resume version", async () => {
      await renderWorkspace();

      const resume = section("Resume");
      expect(within(resume).getByText("Base resume")).toBeInTheDocument();
      expect(within(resume).getByText(/Version 2 · cv-2026\.pdf/)).toBeInTheDocument();
      expect(within(resume).getByText("Confirmed")).toBeInTheDocument();
      expect(within(resume).getByText(/uploading a newer resume doesn't change it/)).toBeInTheDocument();
      // One resume only: nothing to choose from.
      expect(screen.queryByLabelText("Resume version")).not.toBeInTheDocument();
    });

    it("says when the resume hasn't been confirmed yet", async () => {
      await renderWorkspace(
        makeApplication({
          resume: makeApplicationResume({ status: "needs_review", is_confirmed: false }),
        })
      );
      expect(within(section("Resume")).getByText("Not confirmed yet")).toBeInTheDocument();
    });

    it("says so when there is no resume, and points to the upload", async () => {
      listResumesMock.mockResolvedValue([]);
      await renderWorkspace(
        makeApplication({ resume: null, progress: makeProgress(["job"]) })
      );

      const resume = section("Resume");
      expect(within(resume).getByText(/No resume is selected/)).toBeInTheDocument();
      expect(within(resume).getByRole("link", { name: "Upload one" })).toHaveAttribute(
        "href",
        "/profile/resume"
      );
      expect(screen.queryByLabelText("Resume version")).not.toBeInTheDocument();
    });

    it("lets the user choose another version, and only sends it when they do", async () => {
      const user = userEvent.setup();
      listResumesMock.mockResolvedValue([
        makeResume({ id: "resume-2", version_number: 2 }),
        makeResume({ id: "resume-1", version_number: 1, file_name: "cv-old.pdf", status: "uploaded" }),
      ]);
      await renderWorkspace();
      updateApplicationMock.mockResolvedValue(
        makeApplication({
          resume: makeApplicationResume({ id: "resume-1", version_number: 1, file_name: "cv-old.pdf" }),
        })
      );

      const select = screen.getByLabelText("Resume version");
      expect(select).toHaveValue("resume-2");
      expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
        "Version 2 — cv-2026.pdf (confirmed)",
        "Version 1 — cv-old.pdf",
      ]);
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();

      await user.selectOptions(select, "resume-1");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await screen.findByText("Application saved.");
      expect(updateApplicationMock).toHaveBeenCalledWith("app-1", { resume_id: "resume-1" });
      expect(within(section("Resume")).getByText(/Version 1 · cv-old\.pdf/)).toBeInTheDocument();
    });

    it("offers a choice when the application has none yet but resumes exist", async () => {
      listResumesMock.mockResolvedValue([makeResume()]);
      await renderWorkspace(makeApplication({ resume: null, progress: makeProgress(["job"]) }));

      const select = screen.getByLabelText("Resume version");
      expect(select).toHaveValue("");
      expect(within(select).getByRole("option", { name: "Select a resume" })).toBeInTheDocument();
    });

    it("shows a resume the server refuses next to the choice", async () => {
      const user = userEvent.setup();
      listResumesMock.mockResolvedValue([
        makeResume({ id: "resume-2" }),
        makeResume({ id: "resume-1", version_number: 1 }),
      ]);
      await renderWorkspace();
      updateApplicationMock.mockRejectedValue(
        new ApiError(422, "VALIDATION_ERROR", "Resume not found.", { field: "resume_id" })
      );

      await user.selectOptions(screen.getByLabelText("Resume version"), "resume-1");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByText("Resume not found.")).toBeInTheDocument();
    });
  });

  describe("progress", () => {
    it("shows a deterministic checklist", async () => {
      await renderWorkspace();

      const steps = screen.getByRole("list", { name: "Preparation steps" });
      const items = within(steps).getAllByRole("listitem");
      expect(items.map((item) => item.textContent?.replace(/ — (done|to do)$/, ""))).toEqual([
        "✓Job selected",
        "✓Resume selected",
        "○Application language selected",
        "○Salary expectation",
        "○Availability (start date)",
        "○Notice period",
        "○Work authorization (optional)",
      ]);
      expect(items.map((item) => item.getAttribute("data-complete"))).toEqual([
        "true",
        "true",
        "false",
        "false",
        "false",
        "false",
        "false",
      ]);
      expect(screen.getByLabelText("Progress summary")).toHaveTextContent("2 of 6 steps complete");
      const bar = screen.getByRole("progressbar", { name: "Preparation progress" });
      expect(bar).toHaveAttribute("aria-valuenow", "2");
      expect(bar).toHaveAttribute("aria-valuemax", "6");
      expect(screen.getByText(/Application created 2 Oct 2026/)).toBeInTheDocument();
    });

    it("doesn't count the optional step", async () => {
      await renderWorkspace(makePreparedApplication());
      expect(screen.getByLabelText("Progress summary")).toHaveTextContent("6 of 6 steps complete");
      const optional = within(screen.getByRole("list", { name: "Preparation steps" }))
        .getAllByRole("listitem")
        .find((item) => item.getAttribute("data-required") === "false")!;
      expect(optional).toHaveTextContent("Work authorization (optional)");
      expect(optional).toHaveAttribute("data-complete", "true");
    });
  });

  describe("match", () => {
    it("shows the existing match summary and where to see why", async () => {
      await renderWorkspace(makeApplication({ match: makeMatchSummary() }));

      const match = section("Your match");
      expect(within(match).getByLabelText("Overall match")).toHaveTextContent("87%");
      expect(within(match).getByLabelText("Job match")).toHaveTextContent("94%");
      expect(within(match).getByLabelText("Preference match")).toHaveTextContent("71%");
      expect(within(match).getByLabelText("Recommendation")).toHaveTextContent("Strong match");
      expect(within(match).getByRole("link", { name: "See why this job matches" })).toHaveAttribute(
        "href",
        "/jobs/job-1"
      );
      expect(within(match).getByText(/never changes your match/)).toBeInTheDocument();
    });

    it("marks an out-of-date match", async () => {
      await renderWorkspace(
        makeApplication({
          match: makeMatchSummary({ status: "stale", stale_reasons: ["profile_changed"] }),
        })
      );
      expect(within(section("Your match")).getByText(/Out of date: your profile/)).toBeInTheDocument();
    });

    it("never implies a score exists when none was calculated", async () => {
      await renderWorkspace(makeApplication({ match: null }));

      const match = section("Your match");
      expect(within(match).getByText("Match not calculated yet.")).toBeInTheDocument();
      expect(within(match).queryByLabelText("Overall match")).not.toBeInTheDocument();
      expect(within(match).getByRole("link", { name: "Calculate it on the job page" })).toHaveAttribute(
        "href",
        "/jobs/job-1"
      );
    });
  });

  describe("privacy", () => {
    it("tells the user that notes stay private", async () => {
      await renderWorkspace();
      expect(
        screen.getByText("Only you can see these. JobPilot never sends your notes to an employer.")
      ).toBeInTheDocument();
    });

    it("keeps the application separate from the profile", async () => {
      await renderWorkspace();
      expect(screen.getByText(/Your profile and preferences are never changed from here/)).toBeInTheDocument();
    });
  });
});
