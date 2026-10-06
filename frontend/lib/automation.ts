import { valueText } from "@/lib/questions";
import { ApiError } from "@/lib/http";
import {
  ACTIVE_STATUSES,
  type AutomationSession,
  type AutomationStatus,
  type DisplayStatus,
  type FieldAnswer,
  type FormField,
} from "@/types/automation";

/** The session's state in the user's words. */
export const STATUS_DOT: Record<AutomationStatus, string> = {
  STARTING: "bg-amber-400",
  RUNNING: "bg-emerald-500",
  PAUSED: "bg-amber-500",
  USER_CONTROL: "bg-sky-500",
  BLOCKED: "bg-red-500",
  COMPLETED: "bg-emerald-600",
  STOPPED: "bg-slate-400",
  FAILED: "bg-red-600",
};

export function isActive(session: AutomationSession | null | undefined): boolean {
  return !!session && ACTIVE_STATUSES.includes(session.status);
}

/** A field's status as a short label and a colour. */
export const DISPLAY_LABELS: Record<DisplayStatus, string> = {
  filled: "Filled",
  user_modified: "You changed this",
  needs_input: "Needs your input",
  needs_review: "Needs your review",
  action_required: "Needs you",
  ready: "Ready",
  failed: "Fill failed",
  unmapped: "Not matched",
};

export const DISPLAY_STYLES: Record<DisplayStatus, string> = {
  filled: "bg-emerald-50 text-emerald-800 border-emerald-200",
  user_modified: "bg-sky-50 text-sky-800 border-sky-200",
  needs_input: "bg-amber-50 text-amber-800 border-amber-200",
  needs_review: "bg-amber-50 text-amber-800 border-amber-200",
  action_required: "bg-violet-50 text-violet-800 border-violet-200",
  ready: "bg-slate-50 text-slate-700 border-slate-200",
  failed: "bg-red-50 text-red-800 border-red-200",
  unmapped: "bg-slate-50 text-slate-500 border-slate-200",
};

export const SOURCE_LABELS: Record<NonNullable<FieldAnswer["source"]>, string> = {
  USER: "your answer",
  DETERMINISTIC: "from your application data",
  ACCEPTED_AI: "an AI answer you accepted",
};

export const INPUT_TYPE_LABELS: Record<string, string> = {
  text: "Text",
  email: "Email",
  tel: "Phone",
  url: "Link",
  number: "Number",
  date: "Date",
  textarea: "Long text",
  select: "Drop-down",
  radio: "Choice",
  checkbox: "Checkbox",
  checkbox_group: "Checkboxes",
  file: "File",
  custom: "Custom control",
};

/** What the assistant would put into the field, or the suggestion waiting for review. */
export function intendedText(answer: FieldAnswer | null): string {
  return answer ? valueText(answer.text, answer.options) : "";
}

/** The kind of mapping, for the user: how sure JobPilot is that it found the right question. */
export function mappingLabel(field: FormField): string {
  switch (field.mapping.status) {
    case "USER_CONFIRMED":
      return "Confirmed by you";
    case "AUTO_MAPPED":
      return field.mapping.method === "CREATED" ? "New question" : "Matched";
    case "NEEDS_REVIEW":
      return "Needs review";
    default:
      return "Not matched";
  }
}

/** Fields that wait for the user, in the order a person would deal with them. */
export function needsAttention(field: FormField): boolean {
  return (
    field.present &&
    ["needs_review", "needs_input", "action_required", "failed"].includes(field.display_status)
  );
}

export function apiMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** "Fields found: 14" etc., as shown in the panel. */
export function summaryLines(session: AutomationSession): [string, number][] {
  const c = session.counts;
  const lines: [string, number][] = [
    ["Fields found", c.found],
    ["Filled", c.filled],
    ["Needs review", c.needs_review],
    ["Needs input", c.needs_input],
  ];
  if (c.user_modified > 0) lines.push(["You changed", c.user_modified]);
  if (c.action_required > 0) lines.push(["Only you can do", c.action_required]);
  if (c.failed > 0) lines.push(["Failed", c.failed]);
  return lines;
}
