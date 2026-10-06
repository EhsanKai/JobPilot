import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationCard } from "@/components/applications/ApplicationCard";
import { JobCard } from "@/components/jobs/JobCard";
import { JobDetails } from "@/components/jobs/JobDetails";
import { ApiError } from "@/lib/http";

import { makeApplication } from "./fixtures/application";
import { makeJob, makeJobDetail } from "./fixtures/jobs";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

const startApplicationMock = vi.fn();
vi.mock("@/lib/api/applications", () => ({
  startApplication: (...args: unknown[]) => startApplicationMock(...args),
}));

const getJobMock = vi.fn();
vi.mock("@/lib/api/jobs", () => ({
  getJob: (...args: unknown[]) => getJobMock(...args),
  saveJob: vi.fn(),
  unsaveJob: vi.fn(),
}));

const calculateMatchMock = vi.fn();
vi.mock("@/lib/api/match", () => ({
  getMatch: (jobId: string) =>
    Promise.resolve({ job_id: jobId, status: "not_calculated", result: null }),
  calculateMatch: (...args: unknown[]) => calculateMatchMock(...args),
  explainMatch: vi.fn(),
}));

const REF = { id: "app-1", status: "preparing" } as const;

describe("ApplicationCard", () => {
  beforeEach(() => {
    pushMock.mockReset();
    startApplicationMock.mockReset();
  });

  it("offers to start an application, and explains that it doesn't submit anything", () => {
    render(<ApplicationCard jobId="job-1" companyName="Acme GmbH" application={null} />);

    expect(screen.getByRole("button", { name: "Start Application" })).toBeInTheDocument();
    expect(screen.getByText(/Nothing is submitted or sent to Acme GmbH/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Continue Application" })).not.toBeInTheDocument();
  });

  it("creates nothing until the user clicks", () => {
    render(<ApplicationCard jobId="job-1" companyName="Acme GmbH" application={undefined} />);
    expect(startApplicationMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("starts the application and opens its workspace", async () => {
    startApplicationMock.mockResolvedValue(makeApplication({ id: "app-9" }));
    render(<ApplicationCard jobId="job-1" companyName="Acme GmbH" application={null} />);

    await userEvent.click(screen.getByRole("button", { name: "Start Application" }));

    expect(startApplicationMock).toHaveBeenCalledWith("job-1");
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/applications/app-9"));
  });

  it("starts only once on a double click", async () => {
    let resolve: (value: unknown) => void = () => {};
    startApplicationMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<ApplicationCard jobId="job-1" companyName="Acme GmbH" application={null} />);

    await userEvent.dblClick(screen.getByRole("button", { name: "Start Application" }));

    expect(startApplicationMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Start Application" })).toBeDisabled();
    resolve(makeApplication({ id: "app-9" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledTimes(1));
  });

  it("shows a failure and lets the user try again", async () => {
    startApplicationMock.mockRejectedValueOnce(
      new ApiError(500, "INTERNAL_ERROR", "An unexpected error occurred.", {})
    );
    render(<ApplicationCard jobId="job-1" companyName="Acme GmbH" application={null} />);

    await userEvent.click(screen.getByRole("button", { name: "Start Application" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("An unexpected error occurred.");
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start Application" })).toBeEnabled();

    startApplicationMock.mockResolvedValueOnce(makeApplication({ id: "app-2" }));
    await userEvent.click(screen.getByRole("button", { name: "Start Application" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/applications/app-2"));
  });

  it("explains a job that is no longer in the user's list", async () => {
    startApplicationMock.mockRejectedValue(new ApiError(404, "NOT_FOUND", "Job not found.", {}));
    render(<ApplicationCard jobId="job-1" companyName="Acme GmbH" application={null} />);

    await userEvent.click(screen.getByRole("button", { name: "Start Application" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This job is no longer in your job list.");
  });

  it("shows Continue Application when one exists, without creating anything", async () => {
    render(<ApplicationCard jobId="job-1" companyName="Acme GmbH" application={REF} />);

    const link = screen.getByRole("link", { name: "Continue Application" });
    expect(link).toHaveAttribute("href", "/applications/app-1");
    expect(screen.queryByRole("button", { name: "Start Application" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Application status")).toHaveTextContent("In preparation");

    await userEvent.click(link);
    expect(startApplicationMock).not.toHaveBeenCalled();
  });
});

describe("Start / Continue Application on the job details page", () => {
  beforeEach(() => {
    pushMock.mockReset();
    startApplicationMock.mockReset();
    calculateMatchMock.mockReset();
    getJobMock.mockReset();
  });

  it("offers Start Application for a job without one", async () => {
    getJobMock.mockResolvedValue(makeJobDetail({ application: null }));
    render(<JobDetails jobId="job-1" />);

    expect(await screen.findByRole("button", { name: "Start Application" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Continue Application" })).not.toBeInTheDocument();
  });

  it("treats a job from an older API response (no application field) as having none", async () => {
    getJobMock.mockResolvedValue(makeJobDetail());
    render(<JobDetails jobId="job-1" />);

    expect(await screen.findByRole("button", { name: "Start Application" })).toBeInTheDocument();
  });

  it("offers Continue Application for a job that has one", async () => {
    getJobMock.mockResolvedValue(makeJobDetail({ application: REF }));
    render(<JobDetails jobId="job-1" />);

    expect(await screen.findByRole("link", { name: "Continue Application" })).toHaveAttribute(
      "href",
      "/applications/app-1"
    );
    expect(screen.queryByRole("button", { name: "Start Application" })).not.toBeInTheDocument();
  });

  it("never starts an application by itself: not on open, not when matching", async () => {
    getJobMock.mockResolvedValue(makeJobDetail({ saved: false }));
    calculateMatchMock.mockResolvedValue({ job_id: "job-1", status: "not_calculated", result: null });
    render(<JobDetails jobId="job-1" />);

    await screen.findByRole("button", { name: "Start Application" });
    // The match panel loads on its own; calculating is a separate, explicit action.
    await userEvent.click(await screen.findByRole("button", { name: "Calculate match" }));
    await waitFor(() => expect(calculateMatchMock).toHaveBeenCalled());

    expect(startApplicationMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("opens the workspace after Start Application", async () => {
    getJobMock.mockResolvedValue(makeJobDetail());
    startApplicationMock.mockResolvedValue(makeApplication({ id: "app-5" }));
    render(<JobDetails jobId="job-1" />);

    await userEvent.click(await screen.findByRole("button", { name: "Start Application" }));

    expect(startApplicationMock).toHaveBeenCalledWith("job-1");
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/applications/app-5"));
  });
});

describe("JobCard application chip", () => {
  it("links to the application of a job that has one", () => {
    render(<JobCard job={makeJob({ application: REF })} onToggleSave={() => {}} />);

    expect(screen.getByRole("link", { name: "Application: In preparation" })).toHaveAttribute(
      "href",
      "/applications/app-1"
    );
  });

  it("shows nothing for a job without one", () => {
    render(<JobCard job={makeJob({ application: null })} onToggleSave={() => {}} />);
    expect(screen.queryByText(/Application:/)).not.toBeInTheDocument();
  });
});
