/** Application Workspace (V0.7). Mirrors backend/app/schemas/application.py. */

import type { SalaryPeriod, SeniorityLevel } from "@/types/jobs";
import type { MatchSummary } from "@/types/match";
import type { EmploymentType, RemotePreference } from "@/types/preferences";
import type { ResumeStatus } from "@/types/resume";

/**
 * V0.7 only ever creates "preparing". The other values are placeholders for
 * the V0.12 lifecycle, kept so legacy rows still render.
 */
export type ApplicationStatus =
  | "preparing"
  | "draft"
  | "in_progress"
  | "submitted"
  | "interviewing"
  | "offer"
  | "rejected"
  | "withdrawn";

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
