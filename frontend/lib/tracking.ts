import { APPLICATION_STATUS_LABELS } from "@/lib/applications";
import { ApiError } from "@/lib/http";
import { formatDate } from "@/lib/jobs";
import { formatDateTime } from "@/lib/submission";
import type {
  ApplicationStatus,
  StatusCounts,
  SubmissionSummary,
  TimelineEntry,
} from "@/types/application";

// --- Status presentation ---------------------------------------------------------------------

/** What kind of state a status is. Colour is never the only signal: every tone has an icon. */
export type StatusTone = "active" | "positive" | "rejected" | "withdrawn" | "no_response";

export interface StatusPresentation {
  tone: StatusTone;
  /** A text symbol, hidden from screen readers (the label carries the meaning). */
  icon: string;
  className: string;
  /** What the status means, in the user's words. */
  description: string;
}

export const STATUS_PRESENTATION: Record<ApplicationStatus, StatusPresentation> = {
  SAVED: {
    tone: "active",
    icon: "◌",
    className: "bg-slate-100 text-slate-700 border-slate-300",
    description: "You saved the job but have not started preparing an application.",
  },
  PREPARING: {
    tone: "active",
    icon: "✎",
    className: "bg-brand-50 text-brand-700 border-brand-200",
    description: "Your application workspace exists and preparation is under way.",
  },
  APPLIED: {
    tone: "active",
    icon: "➤",
    className: "bg-sky-50 text-sky-800 border-sky-200",
    description: "The application was submitted.",
  },
  FOLLOW_UP: {
    tone: "active",
    icon: "⏲",
    className: "bg-sky-50 text-sky-800 border-sky-200",
    description: "Submitted, and you are waiting or following up.",
  },
  INTERVIEW: {
    tone: "positive",
    icon: "☎",
    className: "bg-emerald-50 text-emerald-800 border-emerald-200",
    description: "The employer invited you to an interview.",
  },
  OFFER: {
    tone: "positive",
    icon: "★",
    className: "bg-emerald-100 text-emerald-900 border-emerald-300",
    description: "The employer made an offer.",
  },
  REJECTED: {
    tone: "rejected",
    icon: "✕",
    className: "bg-red-50 text-red-800 border-red-200",
    description: "The employer rejected the application.",
  },
  WITHDRAWN: {
    tone: "withdrawn",
    icon: "↩",
    className: "bg-amber-50 text-amber-800 border-amber-200",
    description: "You withdrew the application.",
  },
  NO_RESPONSE: {
    tone: "no_response",
    icon: "…",
    className: "bg-slate-100 text-slate-600 border-slate-300 border-dashed",
    description: "You consider the application inactive: nobody replied.",
  },
};

export const ALL_STATUSES = Object.keys(APPLICATION_STATUS_LABELS) as ApplicationStatus[];

/** The statuses shown in the dashboard summary, in this order. */
export const SUMMARY_STATUSES: ApplicationStatus[] = [
  "APPLIED",
  "FOLLOW_UP",
  "INTERVIEW",
  "OFFER",
  "REJECTED",
  "NO_RESPONSE",
];

export function statusLabel(status: ApplicationStatus): string {
  return APPLICATION_STATUS_LABELS[status];
}

// --- Dates -----------------------------------------------------------------------------------

/** "21 Oct 2026", or the fallback for a missing or invalid value. */
export function dateText(value: string | null, fallback = "—"): string {
  return formatDate(value) ?? fallback;
}

/** An ISO date-time for a `<input type="datetime-local">` value ("2026-10-21T09:00"). */
export function toDateTimeLocal(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** The ISO date-time (UTC) of a `datetime-local` value, or null when empty / invalid. */
export function fromDateTimeLocal(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

// --- Submission --------------------------------------------------------------------------------

/** The latest submission attempt, in the user's words (null when there is none). */
export function submissionText(submission: SubmissionSummary | null): string | null {
  if (!submission) return null;
  switch (submission.result) {
    case "SUCCESS":
      return submission.confirmation_reference
        ? `Submitted successfully · confirmation ${submission.confirmation_reference}`
        : "Submitted successfully";
    case "FAILED_BEFORE_SUBMIT":
      return "Not submitted: the attempt failed before the final step";
    case "SUBMISSION_UNKNOWN":
      if (submission.user_verified_outcome === "SUBMITTED") {
        return "Result was uncertain · you confirmed it was submitted";
      }
      if (submission.user_verified_outcome === "NOT_SUBMITTED") {
        return "Result was uncertain · you confirmed it was not submitted";
      }
      return "Submission result uncertain — user verification required";
    default:
      return "Submission in progress";
  }
}

// --- Timeline ----------------------------------------------------------------------------------

export interface TimelineLine {
  /** The headline, e.g. "Applied". */
  title: string;
  /** What happened, in plain words. */
  detail: string;
  /** The user's own note, shown verbatim and never rewritten. */
  note: string | null;
  when: string;
  tone: StatusTone | "neutral" | "warning";
  icon: string;
}

function systemDetail(entry: TimelineEntry): string {
  const status = entry.status;
  if (entry.source === "SUBMISSION") {
    return entry.actor === "USER"
      ? "You confirmed the application was submitted."
      : "Application successfully submitted.";
  }
  if (entry.source === "SYSTEM") {
    if (status === "SAVED") return "Job saved.";
    if (status === "PREPARING") return "Application workspace created.";
    return "Status updated by JobPilot.";
  }
  if (entry.previous_status && status) {
    return `You changed the status from ${statusLabel(entry.previous_status)} to ${statusLabel(status)}.`;
  }
  return "You changed the status.";
}

function submissionLine(entry: TimelineEntry): Omit<TimelineLine, "when"> {
  const submission = entry.submission;
  const base = { note: null };
  if (!submission) {
    return { ...base, title: "Submission", detail: "", tone: "neutral", icon: "➤" };
  }
  switch (submission.result) {
    case "SUCCESS":
      return {
        ...base,
        title: "Application submitted",
        detail: submission.confirmation_reference
          ? `Result: SUCCESS. Confirmation: ${submission.confirmation_reference}`
          : "Result: SUCCESS.",
        tone: "positive",
        icon: "✓",
      };
    case "FAILED_BEFORE_SUBMIT":
      return {
        ...base,
        title: "Submission attempt failed",
        detail: "Nothing was submitted: the attempt failed before the final step.",
        tone: "rejected",
        icon: "!",
      };
    case "SUBMISSION_UNKNOWN": {
      const checked =
        submission.user_verified_outcome === "SUBMITTED"
          ? " You confirmed it was submitted."
          : submission.user_verified_outcome === "NOT_SUBMITTED"
            ? " You confirmed it was not submitted."
            : "";
      return {
        ...base,
        title: "Submission result uncertain",
        detail:
          submission.user_verified_outcome === null
            ? "Submission result uncertain — user verification required."
            : `The result was uncertain.${checked}`,
        tone: "warning",
        icon: "?",
      };
    }
    default:
      return { ...base, title: "Submission in progress", detail: "", tone: "neutral", icon: "…" };
  }
}

/** One timeline entry as a headline, a plain-words detail and (separately) the user's note. */
export function describeTimelineEntry(entry: TimelineEntry): TimelineLine {
  const when = formatDateTime(entry.occurred_at);
  if (entry.kind === "SUBMISSION") {
    return { ...submissionLine(entry), when };
  }
  const status = entry.status ?? "PREPARING";
  const presentation = STATUS_PRESENTATION[status];
  if (entry.kind === "NOTE") {
    return {
      title: "Note",
      detail: `You added a note (status: ${statusLabel(status)}).`,
      note: entry.note,
      when,
      tone: "neutral",
      icon: "✎",
    };
  }
  if (entry.kind === "CORRECTION") {
    return {
      title: `Corrected to ${statusLabel(status)}`,
      detail: entry.previous_status
        ? `You corrected the status from ${statusLabel(entry.previous_status)}.`
        : "You corrected the status.",
      note: entry.note,
      when,
      tone: presentation.tone,
      icon: presentation.icon,
    };
  }
  return {
    title: statusLabel(status),
    detail: systemDetail(entry),
    note: entry.note,
    when,
    tone: presentation.tone,
    icon: presentation.icon,
  };
}

// --- Summary -----------------------------------------------------------------------------------

/** The counts worth showing: only statuses that have at least one application. */
export function summaryRows(
  counts: StatusCounts
): { status: ApplicationStatus; label: string; count: number }[] {
  return SUMMARY_STATUSES.filter((status) => counts.counts[status] > 0).map((status) => ({
    status,
    label: statusLabel(status),
    count: counts.counts[status],
  }));
}

// --- Errors ------------------------------------------------------------------------------------

/** A server error from a tracking action, in the user's words. */
export function trackingErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "This application doesn't exist or isn't yours.";
    if (error.code === "INVALID_STATUS_TRANSITION") {
      return "That status change isn't allowed from the current status. If it was recorded wrongly, use “Correct the status”.";
    }
    return error.message || fallback;
  }
  return fallback;
}
