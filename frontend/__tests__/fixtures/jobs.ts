import type {
  DiscoveryRun,
  DiscoverySummary,
  JobDetail,
  JobList,
  JobSource,
  JobSourcePosting,
  JobSummary,
  SourceRunSummary,
} from "@/types/jobs";

export function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    title: "Senior Backend Engineer (Python)",
    company_name: "Acme GmbH",
    location: "Berlin, Germany",
    work_model: "hybrid",
    employment_type: "full_time",
    seniority: "senior",
    salary_min: "70000.00",
    salary_max: "90000.00",
    salary_currency: "EUR",
    salary_period: "year",
    technologies: ["Python", "PostgreSQL"],
    source: "greenhouse",
    // The API lists the preferred posting's source first.
    sources: [overrides.source ?? "greenhouse"],
    source_count: 1,
    is_active: true,
    canonical_url: "https://job-boards.greenhouse.io/acme/jobs/4012345",
    posted_at: "2026-09-20T08:00:00Z",
    discovered_at: "2026-10-01T10:00:00Z",
    saved: false,
    saved_at: null,
    ...overrides,
  };
}

export function makePosting(overrides: Partial<JobSourcePosting> = {}): JobSourcePosting {
  return {
    id: "posting-1",
    source: "greenhouse",
    source_job_id: "4012345",
    source_company_key: "acme",
    original_url: "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x",
    canonical_url: "https://job-boards.greenhouse.io/acme/jobs/4012345",
    apply_url: null,
    title: "Senior Backend Engineer (Python)",
    company_name: "Acme GmbH",
    location: "Berlin, Germany",
    posted_at: "2026-09-20T08:00:00Z",
    first_seen_at: "2026-10-01T10:00:00Z",
    last_seen_at: "2026-10-01T10:00:00Z",
    is_active: true,
    status: "active",
    is_preferred: true,
    seen_in_latest_run: true,
    ...overrides,
  };
}

export function makeJobDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    ...makeJob(),
    locations: ["Berlin, Germany"],
    country: null,
    city: "Berlin",
    department: "Engineering",
    industry: null,
    description: "About Acme\n\nWe build logistics software.",
    requirements: ["5+ years of backend experience"],
    responsibilities: ["Design and build Python services"],
    salary_text: null,
    source_job_id: "4012345",
    source_company_key: "acme",
    source_url: "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x",
    apply_url: null,
    source_updated_at: "2026-09-28T14:15:00Z",
    last_seen_at: "2026-10-01T10:00:00Z",
    first_discovered_by_you_at: "2026-10-01T10:00:00Z",
    preferred_source_posting_id: "posting-1",
    source_postings: [makePosting()],
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

export function makeList(items: JobSummary[], overrides: Partial<JobList> = {}): JobList {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 20,
    total_pages: items.length ? 1 : 0,
    ...overrides,
  };
}

const ZERO_COUNTS = {
  fetched: 0,
  invalid: 0,
  filtered_out: 0,
  beyond_limit: 0,
  duplicates: 0,
  created: 0,
  merged: 0,
  updated: 0,
  unchanged: 0,
  cross_source_matches: 0,
  possible_matches: 0,
  skipped: 0,
  error_count: 0,
  discovered: 0,
};

export function makeSourceSummary(overrides: Partial<SourceRunSummary> = {}): SourceRunSummary {
  return {
    ...ZERO_COUNTS,
    source: "greenhouse",
    display_name: "Greenhouse",
    status: "succeeded",
    errors: [],
    filters: [],
    ...overrides,
  };
}

export function makeSummary(overrides: Partial<DiscoverySummary> = {}): DiscoverySummary {
  return { ...ZERO_COUNTS, sources: [makeSourceSummary()], ...overrides };
}

export function makeRun(overrides: Partial<DiscoveryRun> = {}): DiscoveryRun {
  return {
    id: "run-1",
    status: "completed",
    is_active: false,
    criteria: {},
    summary: makeSummary(),
    error_code: null,
    error_message: null,
    started_at: "2026-10-01T10:00:00Z",
    finished_at: "2026-10-01T10:00:05Z",
    created_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

export const SOURCES: JobSource[] = [
  {
    name: "greenhouse",
    display_name: "Greenhouse",
    acquisition_method: "ats",
    description: "",
    enabled: true,
    target_count: 3,
    native_filters: [],
    jobpilot_filters: ["keywords"],
  },
  {
    name: "lever",
    display_name: "Lever",
    acquisition_method: "ats",
    description: "",
    enabled: true,
    target_count: 2,
    native_filters: [],
    jobpilot_filters: ["keywords"],
  },
];
