import type { ReviewDocument, ReviewField, SubmissionAttempt, ValueSource } from "@/types/submission";

/** Where a value came from, in the user's words. */
export const SOURCE_LABELS: Record<ValueSource, string> = {
  USER: "Your answer",
  DETERMINISTIC: "From your application data",
  ACCEPTED_AI: "AI-generated → accepted by you",
  DOCUMENT: "The document you chose",
  BROWSER_DISCOVERED: "Already on the page",
};

export function sourceLabel(field: ReviewField): string {
  if (!field.source) return field.value ? "On the page" : "Left empty";
  if (field.source === "USER" && field.verification === "USER_ENTERED") {
    return "Typed by you in the browser";
  }
  return SOURCE_LABELS[field.source];
}

export const VERIFICATION_LABELS: Record<string, string> = {
  VERIFIED: "Checked in the form",
  USER_ENTERED: "Entered by you",
  PAGE_VALUE: "On the page",
  EMPTY: "Empty",
  STALE: "Out of date",
  UNVERIFIABLE: "Cannot be checked",
};

export function fieldValue(field: ReviewField): string {
  return field.value_label ?? field.value ?? "—";
}

const ORIGIN_LABELS: Record<string, string> = {
  ai: "generated",
  humanized: "reworded by Humanize",
  user_edited: "edited by you",
  upload: "your upload",
};

export function documentLabel(doc: ReviewDocument): string {
  if (doc.document_label) {
    return doc.document_label;
  }
  if (doc.file) {
    const name = doc.file.replace(/:\d+$/, "");
    return doc.source === "USER" ? `${name} (attached by you)` : name;
  }
  return "No file";
}

export function originLabel(origin: string | null): string | null {
  return origin ? (ORIGIN_LABELS[origin] ?? origin) : null;
}

export const RESULT_TITLES: Record<NonNullable<SubmissionAttempt["result"]> | "IN_PROGRESS", string> = {
  SUCCESS: "Application submitted successfully.",
  FAILED_BEFORE_SUBMIT: "The application could not be submitted.",
  SUBMISSION_UNKNOWN: "We could not safely determine whether the application was submitted.",
  IN_PROGRESS: "Submitting...",
};

export function formatDateTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return date.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
