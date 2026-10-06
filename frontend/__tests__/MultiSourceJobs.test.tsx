import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DiscoveryStatus } from "@/components/jobs/DiscoveryStatus";
import { JobCard } from "@/components/jobs/JobCard";
import { JobDetails } from "@/components/jobs/JobDetails";
import { sourceIndicator } from "@/lib/jobs";

import { makeJob, makeJobDetail, makePosting, makeRun, makeSummary } from "./fixtures/jobs";

const getJobMock = vi.fn();

vi.mock("@/lib/api/jobs", () => ({
  getJob: (...args: unknown[]) => getJobMock(...args),
  saveJob: vi.fn(),
  unsaveJob: vi.fn(),
}));

const GREENHOUSE_URL = "https://job-boards.greenhouse.io/example/jobs/7001";
const LEVER_URL = "https://jobs.lever.co/example/c0ffee";

function multiSourceJob() {
  return makeJobDetail({
    title: "Senior SAP ABAP Developer",
    company_name: "Example GmbH",
    location: "Cologne, Germany",
    city: "Cologne",
    source: "greenhouse",
    sources: ["greenhouse", "lever"],
    source_count: 2,
    source_company_key: "example",
    canonical_url: GREENHOUSE_URL,
    preferred_source_posting_id: "posting-gh",
    source_postings: [
      makePosting({
        id: "posting-gh",
        source: "greenhouse",
        source_company_key: "example",
        original_url: GREENHOUSE_URL,
        canonical_url: GREENHOUSE_URL,
        title: "Senior SAP ABAP Developer",
        location: "Cologne, Germany",
        is_preferred: true,
      }),
      makePosting({
        id: "posting-lever",
        source: "lever",
        source_job_id: "c0ffee",
        source_company_key: "example",
        original_url: LEVER_URL,
        canonical_url: LEVER_URL,
        apply_url: `${LEVER_URL}/apply`,
        title: "Senior SAP ABAP-Developer (m/w/d)",
        location: "Köln",
        is_preferred: false,
        status: "inactive",
        is_active: false,
      }),
    ],
  });
}

describe("source indicator", () => {
  it("names one or two sources and counts more", () => {
    expect(sourceIndicator({ source: "lever", sources: ["lever"] })).toBe("via Lever");
    expect(sourceIndicator({ source: "greenhouse", sources: ["greenhouse", "lever"] })).toBe(
      "Sources: Greenhouse + Lever"
    );
    expect(
      sourceIndicator({ source: "greenhouse", sources: ["greenhouse", "lever", "other"] })
    ).toBe("Sources: 3");
  });
});

describe("JobCard with several sources", () => {
  it("shows one card with all sources", () => {
    render(
      <JobCard
        job={makeJob({ sources: ["greenhouse", "lever"], source_count: 2 })}
        onToggleSave={() => {}}
      />
    );
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByText("Sources: Greenhouse + Lever")).toBeInTheDocument();
  });

  it("marks jobs no source lists any more", () => {
    render(<JobCard job={makeJob({ is_active: false })} onToggleSave={() => {}} />);
    expect(screen.getByText("No longer listed")).toBeInTheDocument();
  });
});

describe("JobDetails with several sources", () => {
  beforeEach(() => {
    getJobMock.mockReset();
    getJobMock.mockResolvedValue(multiSourceJob());
  });

  it("lists every source with its own original posting link", async () => {
    render(<JobDetails jobId="job-1" />);
    const sources = (await screen.findByRole("heading", { name: "Sources" })).closest("div")!;
    expect(within(sources).getByText("Found on 2 sources.", { exact: false })).toBeInTheDocument();
    const list = within(sources).getByRole("list", { name: "Found on" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);

    const greenhouse = within(list).getByRole("listitem", { name: "Greenhouse" });
    expect(within(greenhouse).getByText("Preferred")).toBeInTheDocument();
    expect(within(greenhouse).getByText("Active")).toBeInTheDocument();
    expect(
      within(greenhouse).getByRole("link", { name: "View original posting on Greenhouse" })
    ).toHaveAttribute("href", GREENHOUSE_URL);

    const lever = within(list).getByRole("listitem", { name: "Lever" });
    expect(within(lever).getByText(/Senior SAP ABAP-Developer \(m\/w\/d\)/)).toBeInTheDocument();
    expect(within(lever).getByText(/in Köln/)).toBeInTheDocument();
    expect(within(lever).getByText("No longer listed")).toBeInTheDocument();
    const leverLink = within(lever).getByRole("link", { name: "View original posting on Lever" });
    expect(leverLink).toHaveAttribute("href", LEVER_URL);
    expect(leverLink).toHaveAttribute("target", "_blank");
    expect(leverLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(lever).getByText("View original posting →")).toBeInTheDocument();
  });

  it("separates JobPilot's normalized data from the source postings", async () => {
    render(<JobDetails jobId="job-1" />);
    const jobPilot = (await screen.findByRole("heading", { name: "JobPilot data" })).closest("div")!;
    expect(within(jobPilot).getByText(/from 2 source postings of this job/)).toBeInTheDocument();
    expect(within(jobPilot).getByText("Preferred source")).toBeInTheDocument();
    expect(within(jobPilot).getByText("Greenhouse (example)")).toBeInTheDocument();
    expect(within(jobPilot).getByText("Greenhouse · Lever")).toBeInTheDocument();
    const posting = screen.getByRole("heading", { name: "Original job posting" }).closest("div")!;
    expect(within(posting).getByText(/\(the preferred source\)/)).toBeInTheDocument();
  });

  it("never renders unsafe source links", async () => {
    const job = multiSourceJob();
    job.source_postings[1] = { ...job.source_postings[1]!, original_url: "javascript:alert(1)" };
    getJobMock.mockResolvedValue(job);
    render(<JobDetails jobId="job-1" />);
    await screen.findByRole("heading", { name: "Sources" });
    for (const link of screen.queryAllByRole("link")) {
      expect(link.getAttribute("href")).not.toMatch(/^javascript:/);
    }
    expect(
      screen.queryByRole("link", { name: "View original posting on Lever" })
    ).not.toBeInTheDocument();
  });

  it("shows the salary as written when it couldn't be structured", async () => {
    getJobMock.mockResolvedValue(
      makeJobDetail({ salary_min: null, salary_max: null, salary_text: "€60k - €70k + bonus" })
    );
    render(<JobDetails jobId="job-1" />);
    expect(await screen.findByText("€60k - €70k + bonus")).toBeInTheDocument();
  });
});

describe("DiscoveryStatus cross-source summary", () => {
  it("reports postings recognized across sources and possible matches kept apart", () => {
    render(
      <DiscoveryStatus
        run={makeRun({
          summary: makeSummary({ discovered: 3, created: 3, merged: 2, possible_matches: 1 }),
        })}
        error={null}
      />
    );
    expect(
      screen.getByText(
        "Discovery finished: 3 matching jobs (3 new, 0 updated). 2 postings were recognized as the same job on another source. 1 similar posting was kept separate to be safe."
      )
    ).toBeInTheDocument();
  });
});
