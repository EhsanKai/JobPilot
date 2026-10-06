import type { Recommendation, StaleReason } from "@/types/match";

/** Badge colours per recommendation (labels come from the API). */
export const RECOMMENDATION_STYLES: Record<Recommendation, string> = {
  STRONG_MATCH: "bg-emerald-50 text-emerald-800",
  GOOD_MATCH: "bg-emerald-50 text-emerald-700",
  POTENTIAL_MATCH: "bg-amber-50 text-amber-800",
  WEAK_MATCH: "bg-slate-100 text-slate-700",
  NOT_RECOMMENDED: "bg-red-50 text-red-800",
  INSUFFICIENT_DATA: "bg-slate-100 text-slate-600",
};

export const STALE_REASON_LABELS: Record<StaleReason, string> = {
  profile_changed: "your profile, preferences or confirmed resume changed",
  job_changed: "the job posting changed",
  algorithm_changed: "JobPilot's matching model was updated",
  taxonomy_changed: "JobPilot's skill and location vocabulary was updated",
};

export const COMPONENT_LABELS: Record<string, string> = {
  skills: "Skills",
  experience: "Experience",
  title: "Role",
  seniority: "Seniority",
  industry: "Industry experience",
  languages: "Languages",
  education: "Education",
  location: "Location",
  work_model: "Work model",
  salary: "Salary",
  employment_type: "Employment type",
  desired_title: "Desired role",
  preferred_industry: "Preferred industry",
  travel: "Travel",
};

export const COMPONENT_STATUS_LABELS: Record<string, string> = {
  match: "Match",
  partial: "Partial",
  mismatch: "Mismatch",
  missing: "Missing",
  unknown: "Unknown",
};

export const IMPORTANCE_LABELS: Record<string, string> = {
  required: "Required",
  preferred: "Nice to have",
  unspecified: "Requested",
  contextual: "Mentioned",
};

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value}%`;
}

export function staleText(reasons: StaleReason[]): string {
  const parts = reasons.map((reason) => STALE_REASON_LABELS[reason]);
  return parts.length ? `Out of date: ${parts.join("; ")}.` : "Out of date.";
}
