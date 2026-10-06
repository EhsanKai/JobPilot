import type {
  Application,
  ApplicationContext,
  ApplicationResume,
  PreparationItem,
  PreparationKey,
  PreparationProgress,
} from "@/types/application";
import type { Resume } from "@/types/resume";

const ORDER: [PreparationKey, boolean][] = [
  ["job", true],
  ["resume", true],
  ["language", true],
  ["salary", true],
  ["availability", true],
  ["notice_period", true],
  ["work_authorization", false],
];

/** A checklist with the given keys complete. */
export function makeProgress(complete: PreparationKey[] = ["job", "resume"]): PreparationProgress {
  const items: PreparationItem[] = ORDER.map(([key, required]) => ({
    key,
    required,
    complete: complete.includes(key),
  }));
  const required = items.filter((item) => item.required);
  return {
    completed: required.filter((item) => item.complete).length,
    total: required.length,
    items,
  };
}

export function makeContext(overrides: Partial<ApplicationContext> = {}): ApplicationContext {
  return {
    schema_version: "application_context_v1",
    origin: "created",
    captured_at: "2026-10-02T09:00:00Z",
    content_hash: "a".repeat(64),
    job: {
      job_id: "job-1",
      canonical_url: "https://job-boards.greenhouse.io/acme/jobs/4012345",
      apply_url: null,
      title: "Senior Backend Engineer (Python)",
      company_name: "Acme GmbH",
      location: "Berlin, Germany",
      locations: ["Berlin, Germany"],
      country: null,
      department: "Engineering",
      description: "About Acme\n\nWe build logistics software.",
      requirements: ["5+ years of backend experience"],
      responsibilities: ["Design and build Python services"],
      technologies: ["Python", "PostgreSQL"],
      salary_min: "70000.00",
      salary_max: "90000.00",
      salary_currency: "EUR",
      salary_period: "year",
      salary_text: null,
      employment_type: "full_time",
      work_model: "hybrid",
      seniority: "senior",
      industry: null,
      posted_at: "2026-09-20T08:00:00Z",
    },
    source: {
      posting_id: "posting-1",
      source: "greenhouse",
      source_job_id: "4012345",
      source_company_key: "acme",
      title: "Senior Backend Engineer (Python)",
      company_name: "Acme GmbH",
      location: "Berlin, Germany",
      posted_at: "2026-09-20T08:00:00Z",
      source_updated_at: "2026-09-28T14:15:00Z",
      original_url: "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x",
      canonical_url: "https://job-boards.greenhouse.io/acme/jobs/4012345",
      apply_url: null,
      application_url: "https://job-boards.greenhouse.io/acme/jobs/4012345",
      was_preferred: true,
    },
    ...overrides,
  };
}

export function makeApplicationResume(
  overrides: Partial<ApplicationResume> = {}
): ApplicationResume {
  return {
    id: "resume-2",
    version_number: 2,
    file_name: "cv-2026.pdf",
    status: "confirmed",
    is_confirmed: true,
    ...overrides,
  };
}

export function makeResume(overrides: Partial<Resume> = {}): Resume {
  return {
    id: "resume-2",
    user_id: "user-1",
    version_number: 2,
    file_name: "cv-2026.pdf",
    mime_type: "application/pdf",
    file_size: 1024,
    status: "confirmed",
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

/** A freshly started application: only what the user didn't have to enter. */
export function makeApplication(overrides: Partial<Application> = {}): Application {
  return {
    id: "app-1",
    status: "preparing",
    job_id: "job-1",
    user_job_id: "user-job-1",
    created_at: "2026-10-02T09:00:00Z",
    updated_at: "2026-10-02T09:00:00Z",
    salary_expectation: null,
    salary_currency: null,
    mention_salary_in_letter: null,
    available_from: null,
    notice_period: null,
    work_authorization: null,
    application_language: null,
    notes: null,
    context: makeContext(),
    source_is_active: true,
    resume: makeApplicationResume(),
    match: null,
    progress: makeProgress(),
    ...overrides,
  };
}

/** An application with every preparation field filled in. */
export function makePreparedApplication(overrides: Partial<Application> = {}): Application {
  return makeApplication({
    salary_expectation: "78000.00",
    salary_currency: "EUR",
    mention_salary_in_letter: true,
    available_from: "2027-01-01",
    notice_period: { value: 3, unit: "months" },
    work_authorization: "authorized",
    application_language: "de",
    notes: "Referral from Alex.",
    progress: makeProgress([
      "job",
      "resume",
      "language",
      "salary",
      "availability",
      "notice_period",
      "work_authorization",
    ]),
    ...overrides,
  });
}
