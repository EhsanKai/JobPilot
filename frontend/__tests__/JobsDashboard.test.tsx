import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobsDashboard } from "@/components/jobs/JobsDashboard";
import { ApiError } from "@/lib/http";

import {
  SOURCES,
  makeJob,
  makeList,
  makeRun,
  makeSourceSummary,
  makeSummary,
} from "./fixtures/jobs";

const listJobsMock = vi.fn();
const listJobSourcesMock = vi.fn();
const getLatestDiscoveryRunMock = vi.fn();
const getDiscoveryRunMock = vi.fn();
const startDiscoveryMock = vi.fn();
const saveJobMock = vi.fn();
const unsaveJobMock = vi.fn();

vi.mock("@/lib/api/jobs", () => ({
  listJobs: (...args: unknown[]) => listJobsMock(...args),
  listJobSources: () => listJobSourcesMock(),
  getLatestDiscoveryRun: () => getLatestDiscoveryRunMock(),
  getDiscoveryRun: (...args: unknown[]) => getDiscoveryRunMock(...args),
  startDiscovery: (...args: unknown[]) => startDiscoveryMock(...args),
  saveJob: (...args: unknown[]) => saveJobMock(...args),
  unsaveJob: (...args: unknown[]) => unsaveJobMock(...args),
}));

function renderDashboard() {
  return render(<JobsDashboard pollIntervalMs={5} />);
}

describe("JobsDashboard", () => {
  beforeEach(() => {
    for (const mock of [
      listJobsMock,
      listJobSourcesMock,
      getLatestDiscoveryRunMock,
      getDiscoveryRunMock,
      startDiscoveryMock,
      saveJobMock,
      unsaveJobMock,
    ]) {
      mock.mockReset();
    }
    listJobSourcesMock.mockResolvedValue(SOURCES);
    getLatestDiscoveryRunMock.mockResolvedValue(null);
    listJobsMock.mockResolvedValue(makeList([makeJob()]));
  });

  it("shows a loading state, then job cards", async () => {
    let resolve: (value: unknown) => void = () => {};
    listJobsMock.mockReturnValue(new Promise((r) => (resolve = r)));
    renderDashboard();
    expect(screen.getByText("Loading jobs...")).toBeInTheDocument();

    resolve(makeList([makeJob()]));
    const card = await screen.findByRole("article", { name: "Senior Backend Engineer (Python)" });
    expect(within(card).getByText("Acme GmbH")).toBeInTheDocument();
    expect(within(card).getByText("via Greenhouse")).toBeInTheDocument();
    expect(within(card).getByText("Hybrid")).toBeInTheDocument();
    expect(within(card).getByText("Full-time")).toBeInTheDocument();
    expect(within(card).getByText("€70,000 – €90,000 per year")).toBeInTheDocument();
    expect(within(card).getByText(/Posted 20 Sept? 2026/)).toBeInTheDocument();
  });

  it("shows an empty state that suggests discovery", async () => {
    listJobsMock.mockResolvedValue(makeList([]));
    renderDashboard();
    expect(await screen.findByText("No jobs found")).toBeInTheDocument();
    expect(screen.getByText(/hasn't found any jobs for you yet/)).toBeInTheDocument();
  });

  it("shows an error state when jobs can't be loaded", async () => {
    listJobsMock.mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "Server unavailable", {}));
    renderDashboard();
    expect(await screen.findByRole("alert")).toHaveTextContent("Server unavailable");
  });

  it("offers enabled sources in the source filter", async () => {
    renderDashboard();
    const select = await screen.findByLabelText("Source");
    await waitFor(() =>
      expect(within(select).getByRole("option", { name: "Lever" })).toBeInTheDocument()
    );
  });

  it("filters stored jobs with the search controls", async () => {
    const user = userEvent.setup();
    renderDashboard();
    await screen.findByRole("article");

    await user.type(screen.getByLabelText("Keywords"), "python");
    await user.type(screen.getByLabelText("Location"), "Berlin");
    await user.selectOptions(screen.getByLabelText("Work model"), "remote");
    await user.selectOptions(screen.getByLabelText("Employment type"), "full_time");
    await user.click(screen.getByRole("button", { name: "Filter my jobs" }));

    await waitFor(() =>
      expect(listJobsMock).toHaveBeenLastCalledWith(
        expect.objectContaining({
          q: "python",
          location: "Berlin",
          work_model: "remote",
          employment_type: "full_time",
          page: 1,
        })
      )
    );
    expect(startDiscoveryMock).not.toHaveBeenCalled();
  });

  it("explains when filters match nothing", async () => {
    const user = userEvent.setup();
    renderDashboard();
    await screen.findByRole("article");
    listJobsMock.mockResolvedValue(makeList([]));
    await user.type(screen.getByLabelText("Keywords"), "cobol");
    await user.click(screen.getByRole("button", { name: "Filter my jobs" }));
    expect(await screen.findByText(/No jobs match these filters/)).toBeInTheDocument();
  });

  it("runs discovery with the current filters, shows progress, then the summary", async () => {
    const user = userEvent.setup();
    listJobsMock.mockResolvedValueOnce(makeList([]));
    startDiscoveryMock.mockResolvedValue(makeRun({ status: "pending", is_active: true, summary: null }));
    getDiscoveryRunMock
      .mockResolvedValueOnce(makeRun({ status: "running", is_active: true, summary: null }))
      .mockResolvedValueOnce(
        makeRun({ summary: makeSummary({ discovered: 2, created: 2 }) })
      );
    listJobsMock.mockResolvedValue(makeList([makeJob(), makeJob({ id: "job-2", title: "Frontend Engineer", source: "lever" })]));
    renderDashboard();
    await screen.findByText("No jobs found");

    await user.type(screen.getByLabelText("Keywords"), "python, typescript");
    await user.selectOptions(screen.getByLabelText("Source"), "lever");
    await user.click(screen.getByRole("button", { name: "Discover jobs" }));

    expect(startDiscoveryMock).toHaveBeenCalledWith(
      expect.objectContaining({ keywords: ["python", "typescript"], sources: ["lever"] })
    );
    expect(await screen.findByText(/Discovering jobs/)).toBeInTheDocument();
    expect(
      await screen.findByText("Discovery finished: 2 matching jobs (2 new, 0 updated).")
    ).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Frontend Engineer" })).toBeInTheDocument();
    expect(getDiscoveryRunMock).toHaveBeenCalledWith("run-1");
  });

  it("can discover using the saved job preferences", async () => {
    const user = userEvent.setup();
    startDiscoveryMock.mockResolvedValue(makeRun({ status: "pending", is_active: true, summary: null }));
    getDiscoveryRunMock.mockResolvedValue(makeRun());
    renderDashboard();
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "Discover with my preferences" }));
    expect(startDiscoveryMock).toHaveBeenCalledWith(undefined);
  });

  it("reports partial source failures", async () => {
    const user = userEvent.setup();
    startDiscoveryMock.mockResolvedValue(makeRun({ status: "pending", is_active: true, summary: null }));
    getDiscoveryRunMock.mockResolvedValue(
      makeRun({
        status: "partial",
        summary: makeSummary({
          discovered: 3,
          created: 3,
          sources: [
            makeSourceSummary({ discovered: 3, created: 3 }),
            makeSourceSummary({
              source: "lever",
              display_name: "Lever",
              status: "failed",
              errors: [{ code: "SOURCE_UNAVAILABLE", message: "Unable to reach the job source.", target: "globex" }],
            }),
          ],
        }),
      })
    );
    renderDashboard();
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "Discover jobs" }));

    expect(await screen.findByText("Some sources succeeded, some failed.")).toBeInTheDocument();
    expect(screen.getByText("Lever (globex): Unable to reach the job source.")).toBeInTheDocument();
  });

  it("reports when no source could be reached", async () => {
    getLatestDiscoveryRunMock.mockResolvedValue(
      makeRun({
        status: "failed",
        error_code: "ALL_SOURCES_FAILED",
        error_message: "No job source could be reached. Please try again later.",
        summary: makeSummary({ sources: [makeSourceSummary({ status: "failed", errors: [{ code: "SOURCE_TIMEOUT", message: "The job source took too long to respond.", target: "acme" }] })] }),
      })
    );
    renderDashboard();
    expect(await screen.findByText("Unable to reach the job sources.")).toBeInTheDocument();
    expect(screen.getByText(/took too long to respond/)).toBeInTheDocument();
  });

  it("tells the user when no filter could be applied by any source", async () => {
    getLatestDiscoveryRunMock.mockResolvedValue(
      makeRun({
        summary: makeSummary({
          sources: [makeSourceSummary({ filters: [{ filter: "industries", applied_by: "not_applied" }] })],
        }),
      })
    );
    renderDashboard();
    expect(await screen.findByText(/No source could filter by industries/)).toBeInTheDocument();
  });

  it("resumes showing progress for a run that is already active", async () => {
    getLatestDiscoveryRunMock.mockResolvedValue(makeRun({ status: "running", is_active: true, summary: null }));
    getDiscoveryRunMock.mockResolvedValue(makeRun({ summary: makeSummary({ discovered: 0 }) }));
    renderDashboard();
    expect(await screen.findByText(/Discovering jobs/)).toBeInTheDocument();
    expect(await screen.findByText(/no matching jobs found this time/)).toBeInTheDocument();
  });

  it("follows the already-running run when discovery is started twice", async () => {
    const user = userEvent.setup();
    startDiscoveryMock.mockRejectedValue(
      new ApiError(409, "CONFLICT", "Job discovery is already running.", { run_id: "run-9" })
    );
    getDiscoveryRunMock.mockResolvedValue(makeRun({ id: "run-9" }));
    renderDashboard();
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "Discover jobs" }));
    await waitFor(() => expect(getDiscoveryRunMock).toHaveBeenCalledWith("run-9"));
  });

  it("shows an error when discovery can't start", async () => {
    const user = userEvent.setup();
    startDiscoveryMock.mockRejectedValue(
      new ApiError(422, "VALIDATION_ERROR", "No job sources are enabled.", {})
    );
    renderDashboard();
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "Discover jobs" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No job sources are enabled.");
  });

  it("saves and unsaves a job", async () => {
    const user = userEvent.setup();
    saveJobMock.mockResolvedValue({ ...makeJob(), saved: true, saved_at: "2026-10-02T00:00:00Z" });
    unsaveJobMock.mockResolvedValue({ ...makeJob(), saved: false, saved_at: null });
    renderDashboard();
    await screen.findByRole("article");

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(saveJobMock).toHaveBeenCalledWith("job-1");
    const saved = await screen.findByRole("button", { name: "Saved ✓" });
    expect(saved).toHaveAttribute("aria-pressed", "true");

    await user.click(saved);
    expect(unsaveJobMock).toHaveBeenCalledWith("job-1");
    expect(await screen.findByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("shows saved jobs only", async () => {
    const user = userEvent.setup();
    renderDashboard();
    await screen.findByRole("article");
    await user.click(screen.getByLabelText("Saved only"));
    await waitFor(() =>
      expect(listJobsMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ saved: true, sort: "saved", page: 1 })
      )
    );
    expect(screen.getByRole("heading", { name: /Saved jobs/ })).toBeInTheDocument();
  });

  it("paginates", async () => {
    const user = userEvent.setup();
    listJobsMock.mockResolvedValue(makeList([makeJob()], { total: 45, total_pages: 3 }));
    renderDashboard();
    expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    listJobsMock.mockResolvedValue(makeList([makeJob()], { total: 45, total_pages: 3, page: 2 }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(listJobsMock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
    );
    expect(await screen.findByText("Page 2 of 3")).toBeInTheDocument();
  });
});
