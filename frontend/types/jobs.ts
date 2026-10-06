import type { MatchSummary } from "@/types/match";
import type { EmploymentType, RemotePreference } from "@/types/preferences";

export type SeniorityLevel =
  | "intern"
  | "junior"
  | "mid"
  | "senior"
  | "lead"
  | "manager"
  | "director";

export type SalaryPeriod = "year" | "month" | "week" | "day" | "hour";

export type SearchFilter =
  | "keywords"
  | "job_titles"
  | "locations"
  | "work_models"
  | "employment_types"
  | "industries"
  | "salary"
  | "posted_after";

export interface JobSearchCriteria {
  keywords?: string[];
  job_titles?: string[];
  locations?: string[];
  work_models?: RemotePreference[];
  employment_types?: EmploymentType[];
  industries?: string[];
  salary_min?: string | number | null;
  salary_max?: string | number | null;
  salary_currency?: string | null;
  posted_after?: string | null;
  sources?: string[] | null;
  page?: number;
  limit?: number;
}

export interface JobSummary {
  id: string;
  title: string;
  company_name: string;
  location: string | null;
  work_model: RemotePreference | null;
  employment_type: EmploymentType | null;
  seniority: SeniorityLevel | null;
  salary_min: string | null;
  salary_max: string | null;
  salary_currency: string | null;
  salary_period: SalaryPeriod | null;
  technologies: string[];
  /** Source of the preferred posting. */
  source: string;
  /** Every source the job was found on, preferred first (V0.4). */
  sources: string[];
  source_count: number;
  /** False when no source lists the job any more. */
  is_active: boolean;
  canonical_url: string;
  posted_at: string | null;
  discovered_at: string;
  saved: boolean;
  saved_at: string | null;
  /** Your match for this job (V0.6); null when none was calculated. */
  match?: MatchSummary | null;
}

export type PostingStatus = "active" | "stale" | "inactive";

/** Where a job was found: one posting at one source, as that source stated it. */
export interface JobSourcePosting {
  id: string;
  source: string;
  source_job_id: string | null;
  source_company_key: string | null;
  original_url: string;
  canonical_url: string;
  apply_url: string | null;
  title: string;
  company_name: string;
  location: string | null;
  posted_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  is_active: boolean;
  status: PostingStatus;
  is_preferred: boolean;
  seen_in_latest_run: boolean | null;
}

export interface JobDetail extends JobSummary {
  locations: string[];
  country: string | null;
  city: string | null;
  department: string | null;
  industry: string | null;
  description: string | null;
  requirements: string[];
  responsibilities: string[];
  salary_text: string | null;
  source_job_id: string | null;
  source_company_key: string | null;
  source_url: string | null;
  apply_url: string | null;
  source_updated_at: string | null;
  last_seen_at: string;
  first_discovered_by_you_at: string | null;
  preferred_source_posting_id: string | null;
  source_postings: JobSourcePosting[];
  created_at: string;
  updated_at: string;
}

export interface JobList {
  items: JobSummary[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export type JobSort = "posted" | "discovered" | "saved" | "match";

export interface JobListQuery {
  page?: number;
  page_size?: number;
  source?: string;
  q?: string;
  location?: string;
  work_model?: RemotePreference;
  employment_type?: EmploymentType;
  posted_after?: string;
  saved?: boolean;
  sort?: JobSort;
}

export type DiscoveryRunStatus = "pending" | "running" | "completed" | "partial" | "failed";
export type SourceRunStatus = "succeeded" | "partial" | "failed";
export type FilterAppliedBy = "source" | "jobpilot" | "not_applied";

export interface DiscoveryCounts {
  fetched: number;
  invalid: number;
  filtered_out: number;
  beyond_limit: number;
  duplicates: number;
  /** New postings that became a new canonical job. */
  created: number;
  /** New postings attached to a job another source already listed. */
  merged: number;
  updated: number;
  unchanged: number;
  /** Stored postings whose job is listed by two or more sources. */
  cross_source_matches: number;
  /** Postings that resembled an existing job but were kept separate. */
  possible_matches: number;
  skipped: number;
  error_count: number;
  /** HTTP requests retried after a transient failure (bounded). */
  retried_requests: number;
  /** Distinct canonical jobs stored for the user. */
  discovered: number;
}

export interface SourceIssue {
  code: string;
  message: string;
  target: string | null;
}

export interface SourceRunSummary extends DiscoveryCounts {
  source: string;
  display_name: string;
  status: SourceRunStatus;
  errors: SourceIssue[];
  filters: { filter: SearchFilter; applied_by: FilterAppliedBy }[];
}

export interface DiscoverySummary extends DiscoveryCounts {
  sources: SourceRunSummary[];
}

export interface DiscoveryRun {
  id: string;
  status: DiscoveryRunStatus;
  is_active: boolean;
  criteria: JobSearchCriteria;
  summary: DiscoverySummary | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface JobSource {
  name: string;
  display_name: string;
  acquisition_method: string;
  description: string;
  enabled: boolean;
  target_count: number;
  native_filters: SearchFilter[];
  jobpilot_filters: SearchFilter[];
}
