/** Application Workspace (V0.7). Mirrors backend/app/schemas/application.py. */

import type { SalaryPeriod, SeniorityLevel } from "@/types/jobs";
import type { MatchSummary } from "@/types/match";
import type { EmploymentType, RemotePreference } from "@/types/preferences";
import type { ResumeStatus } from "@/types/resume";
import type { SubmissionResult } from "@/types/submission";

/**
 * Where an application is in its tracking lifecycle (V0.12). Mirrors the backend
 * `ApplicationStatus`; which change is allowed from which status is decided by the server
 * (`tracking.allowed_next_statuses`).
 */
export type ApplicationStatus =
  | "SAVED"
  | "PREPARING"
  | "APPLIED"
  | "FOLLOW_UP"
  | "INTERVIEW"
  | "OFFER"
  | "REJECTED"
  | "WITHDRAWN"
  | "NO_RESPONSE";

export type NoticePeriodUnit = "days" | "weeks" | "months";
export type WorkAuthorization = "authorized" | "requires_sponsorship" | "unknown";

/** ISO 639-1 keys of the languages an application can be written in. */
export type ApplicationLanguage = "de" | "en";

export interface NoticePeriod {
  value: number;
  unit: NoticePeriodUnit;
}

/** Your application for a job, as embedded in job list / details. */
export interface ApplicationRef {
  id: string;
  status: ApplicationStatus;
}

export type PreparationKey =
  | "job"
  | "resume"
  | "language"
  | "salary"
  | "availability"
  | "notice_period"
  | "work_authorization";

export interface PreparationItem {
  key: PreparationKey;
  complete: boolean;
  /** Optional items are shown but not counted. */
  required: boolean;
}

export interface PreparationProgress {
  completed: number;
  total: number;
  items: PreparationItem[];
}

export interface ApplicationResume {
  id: string;
  version_number: number;
  file_name: string;
  status: ResumeStatus;
  is_confirmed: boolean;
}

/** The job as it was when the application was started. */
export interface ContextJob {
  job_id: string;
  canonical_url: string;
  apply_url: string | null;
  title: string;
  company_name: string;
  location: string | null;
  locations: string[];
  country: string | null;
  department: string | null;
  description: string | null;
  requirements: string[];
  responsibilities: string[];
  technologies: string[];
  salary_min: string | null;
  salary_max: string | null;
  salary_currency: string | null;
  salary_period: SalaryPeriod | null;
  salary_text: string | null;
  employment_type: EmploymentType | null;
  work_model: RemotePreference | null;
  seniority: SeniorityLevel | null;
  industry: string | null;
  posted_at: string | null;
}

/** The source posting chosen as the application target. */
export interface ContextSource {
  posting_id: string | null;
  source: string;
  source_job_id: string | null;
  source_company_key: string | null;
  title: string;
  company_name: string;
  location: string | null;
  posted_at: string | null;
  source_updated_at: string | null;
  /** The posting URL as the source returned it: "View original posting". */
  original_url: string;
  canonical_url: string;
  apply_url: string | null;
  /** Where applying happens; null when unknown. */
  application_url: string | null;
  was_preferred: boolean;
}

export interface ApplicationContext {
  schema_version: string;
  origin: "created" | "migrated";
  captured_at: string;
  content_hash: string;
  job: ContextJob;
  source: ContextSource;
}

export interface Application {
  id: string;
  status: ApplicationStatus;
  job_id: string;
  user_job_id: string;
  created_at: string;
  updated_at: string;

  /** Annual gross, as a decimal string ("78000.00"). */
  salary_expectation: string | null;
  salary_currency: string | null;
  /** null = not decided. */
  mention_salary_in_letter: boolean | null;
  /** YYYY-MM-DD */
  available_from: string | null;
  notice_period: NoticePeriod | null;
  work_authorization: WorkAuthorization | null;
  application_language: ApplicationLanguage | null;
  notes: string | null;

  context: ApplicationContext;
  /** Whether the selected posting is still listed by its source; null if it is gone. */
  source_is_active: boolean | null;
  resume: ApplicationResume | null;
  /** Your current match for the job; never recalculated by the workspace. */
  match: MatchSummary | null;
  progress: PreparationProgress;
  /** Where the application is in its tracking lifecycle (V0.12). */
  tracking: ApplicationTracking;
}

/** The latest V0.11 submission attempt, as tracking shows it. */
export interface SubmissionSummary {
  attempt_id: string;
  result: SubmissionResult | null;
  started_at: string;
  finished_at: string | null;
  confirmation_reference: string | null;
  /** After an unknown result: what the user found when they checked. */
  user_verified_outcome: "SUBMITTED" | "NOT_SUBMITTED" | null;
  /** The result is uncertain and the user has not checked yet. */
  needs_verification: boolean;
}

export interface ApplicationTracking {
  status: ApplicationStatus;
  applied_at: string | null;
  last_status_change_at: string;
  /** A date the user set for themselves: nothing is sent when it passes. */
  follow_up_at: string | null;
  /** What an ordinary status change may go to from here (the server decides). */
  allowed_next_statuses: ApplicationStatus[];
  submission: SubmissionSummary | null;
}

export interface ApplicationSummary {
  id: string;
  status: ApplicationStatus;
  job_id: string;
  title: string;
  company_name: string;
  location: string | null;
  source: string;
  original_url: string;
  created_at: string;
  updated_at: string;
  progress: PreparationProgress;
  applied_at: string | null;
  last_status_change_at: string;
  follow_up_at: string | null;
  submission: SubmissionSummary | null;
}

export interface ApplicationList {
  items: ApplicationSummary[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/** The application-specific data a user can set. */
export interface ApplicationPreparationInput {
  salary_expectation?: number | string | null;
  salary_currency?: string | null;
  mention_salary_in_letter?: boolean | null;
  available_from?: string | null;
  notice_period?: NoticePeriod | null;
  work_authorization?: WorkAuthorization | null;
  application_language?: ApplicationLanguage | null;
  notes?: string | null;
}

/** Body of `POST /jobs/{id}/application` (all optional; only used when creating). */
export interface ApplicationStartRequest extends ApplicationPreparationInput {
  resume_id?: string | null;
  source_posting_id?: string | null;
}

/** Body of `PATCH /applications/{id}`: omitted = unchanged, null = cleared. */
export interface ApplicationUpdateRequest extends ApplicationPreparationInput {
  resume_id?: string;
}

// --- Tracking (V0.12) ------------------------------------------------------------------------

export type ApplicationSort = "updated" | "applied";

/** Filters and sorting of `GET /applications` ("My Applications"). */
export interface ApplicationListQuery {
  page?: number;
  pageSize?: number;
  statuses?: ApplicationStatus[];
  company?: string;
  source?: string;
  /** YYYY-MM-DD, inclusive. */
  appliedFrom?: string;
  appliedTo?: string;
  sort?: ApplicationSort;
}

/** Body of `PATCH /applications/{id}/status`. The server decides everything else. */
export interface StatusChangeRequest {
  status: ApplicationStatus;
  note?: string;
  /** Explicitly fix a wrongly recorded status: any other status, a note is required. */
  correction?: boolean;
  /** ISO date-time. Only while waiting (applied, follow-up, interview). */
  follow_up_at?: string;
}

/** Body of `PATCH /applications/{id}/tracking`: omitted = unchanged, null = cleared. */
export interface TrackingUpdateRequest {
  follow_up_at?: string | null;
  note?: string;
}

export interface StatusChangeResponse {
  /** False when the request changed nothing. */
  changed: boolean;
  tracking: ApplicationTracking;
}

export type TimelineKind = "STATUS_CHANGE" | "CORRECTION" | "NOTE" | "SUBMISSION";
export type StatusChangeSource = "SYSTEM" | "USER" | "SUBMISSION";
export type StatusChangeActor = "USER" | "SYSTEM";

export interface TimelineSubmission {
  attempt_id: string;
  result: SubmissionResult | null;
  destination_domain: string;
  confirmation_reference: string | null;
  confirmation_summary: string | null;
  user_verified_outcome: "SUBMITTED" | "NOT_SUBMITTED" | null;
  user_verified_at: string | null;
  failure_code: string | null;
}

export interface TimelineEntry {
  kind: TimelineKind;
  id: string;
  occurred_at: string;
  previous_status: ApplicationStatus | null;
  status: ApplicationStatus | null;
  source: StatusChangeSource | null;
  actor: StatusChangeActor | null;
  /** The user's own words, exactly as written. */
  note: string | null;
  submission_attempt_id: string | null;
  submission: TimelineSubmission | null;
}

export interface Timeline {
  /** Newest first. */
  items: TimelineEntry[];
}

export interface StatusCounts {
  counts: Record<ApplicationStatus, number>;
  total: number;
}
