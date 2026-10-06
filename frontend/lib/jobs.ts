import type { EmploymentType, RemotePreference } from "@/types/preferences";
import type { JobSummary, PostingStatus, SalaryPeriod, SeniorityLevel } from "@/types/jobs";

export const WORK_MODEL_LABELS: Record<RemotePreference, string> = {
  onsite: "On-site",
  remote: "Remote",
  hybrid: "Hybrid",
  flexible: "Flexible",
};

export const EMPLOYMENT_TYPE_LABELS: Record<EmploymentType, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
  internship: "Internship",
  temporary: "Temporary",
  freelance: "Freelance",
  working_student: "Working student",
};

export const SENIORITY_LABELS: Record<SeniorityLevel, string> = {
  intern: "Intern / Student",
  junior: "Junior",
  mid: "Mid-level",
  senior: "Senior",
  lead: "Lead / Staff",
  manager: "Manager",
  director: "Director",
};

const PERIOD_LABELS: Record<SalaryPeriod, string> = {
  year: "per year",
  month: "per month",
  week: "per week",
  day: "per day",
  hour: "per hour",
};

export function sourceLabel(source: string): string {
  const known: Record<string, string> = { greenhouse: "Greenhouse", lever: "Lever" };
  return known[source] ?? source;
}

export const POSTING_STATUS_LABELS: Record<PostingStatus, string> = {
  active: "Active",
  stale: "Not seen recently",
  inactive: "No longer listed",
};

/**
 * Where a job was found, for the job list: "via Greenhouse" for one source,
 * "Sources: Greenhouse + Lever" for two, "Sources: 3" for more.
 */
export function sourceIndicator(job: Pick<JobSummary, "source" | "sources">): string {
  const sources = job.sources && job.sources.length > 0 ? job.sources : [job.source];
  if (sources.length <= 1) return `via ${sourceLabel(sources[0] ?? job.source)}`;
  if (sources.length === 2) return `Sources: ${sources.map(sourceLabel).join(" + ")}`;
  return `Sources: ${sources.length}`;
}

function formatAmount(value: string, currency: string | null): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return value;
  if (currency) {
    try {
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        maximumFractionDigits: 0,
      }).format(amount);
    } catch {
      // Unknown currency code: fall through to a plain number.
    }
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(amount);
}

/** "€70,000 – €90,000 per year", or null when the posting states no salary. */
export function formatSalary(
  job: Pick<JobSummary, "salary_min" | "salary_max" | "salary_currency" | "salary_period">
): string | null {
  const { salary_min: min, salary_max: max, salary_currency: currency, salary_period: period } = job;
  if (min === null && max === null) return null;
  let text: string;
  if (min !== null && max !== null && min !== max) {
    text = `${formatAmount(min, currency)} – ${formatAmount(max, currency)}`;
  } else if (min !== null && max === null) {
    text = `from ${formatAmount(min, currency)}`;
  } else if (min === null && max !== null) {
    text = `up to ${formatAmount(max, currency)}`;
  } else {
    text = formatAmount(min as string, currency);
  }
  return period ? `${text} ${PERIOD_LABELS[period]}` : text;
}

export function formatDate(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/**
 * Only http(s) links are rendered as clickable. The backend already
 * validates URLs; this is defense in depth against e.g. `javascript:` links.
 */
export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

/** ISO date (YYYY-MM-DD) `days` days before `now`. */
export function daysAgoIso(days: number, now: Date = new Date()): string {
  const date = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
  return date.toISOString().slice(0, 10);
}
