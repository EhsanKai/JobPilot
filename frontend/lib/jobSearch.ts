import { daysAgoIso } from "@/lib/jobs";
import type { JobListQuery, JobSearchCriteria } from "@/types/jobs";
import type { EmploymentType, RemotePreference } from "@/types/preferences";

/** The one set of controls on the Jobs page. It filters the stored list and
 * drives discovery, so "what I see" and "what I search for" never diverge. */
export interface JobSearchValues {
  keyword: string;
  location: string;
  workModel: RemotePreference | "";
  employmentType: EmploymentType | "";
  source: string;
  /** Days, as a string from the <select>; "" = any time. */
  postedWithin: string;
}

export const EMPTY_SEARCH: JobSearchValues = {
  keyword: "",
  location: "",
  workModel: "",
  employmentType: "",
  source: "",
  postedWithin: "",
};

function splitTerms(value: string): string[] {
  return value
    .split(",")
    .map((term) => term.trim())
    .filter(Boolean);
}

export function toListQuery(values: JobSearchValues, now?: Date): JobListQuery {
  return {
    q: values.keyword.trim() || undefined,
    location: values.location.trim() || undefined,
    work_model: values.workModel || undefined,
    employment_type: values.employmentType || undefined,
    source: values.source || undefined,
    posted_after: values.postedWithin ? daysAgoIso(Number(values.postedWithin), now) : undefined,
  };
}

/** Discovery criteria. Comma-separated keywords/locations become separate
 * terms (any of them may match). */
export function toCriteria(values: JobSearchValues, now?: Date): JobSearchCriteria {
  return {
    keywords: splitTerms(values.keyword),
    locations: splitTerms(values.location),
    work_models: values.workModel ? [values.workModel] : [],
    employment_types: values.employmentType ? [values.employmentType] : [],
    sources: values.source ? [values.source] : null,
    posted_after: values.postedWithin ? daysAgoIso(Number(values.postedWithin), now) : null,
  };
}
