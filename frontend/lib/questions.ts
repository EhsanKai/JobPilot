import { violationLabel } from "@/lib/documents";
import { ApiError } from "@/lib/http";
import type { DocumentViolation } from "@/types/documents";
import type { Answer, AnswerStatus, Question, QuestionType } from "@/types/questions";

export const TYPE_LABELS: Record<QuestionType, string> = {
  FREE_TEXT: "Free text",
  YES_NO: "Yes / No",
  SINGLE_CHOICE: "Single choice",
  MULTI_CHOICE: "Multiple choice",
  NUMERIC: "Number",
  DATE: "Date",
  SALARY: "Salary",
  UNKNOWN: "Unknown",
};

export const CHOICE_TYPES: QuestionType[] = ["SINGLE_CHOICE", "MULTI_CHOICE"];

/** Types that take a list of options (a yes/no may label its two). */
export function takesOptions(type: QuestionType): boolean {
  return CHOICE_TYPES.includes(type) || type === "YES_NO";
}

/** The text of an answer or suggestion: its text, or its options joined. */
export function valueText(text: string | null, options: string[] | null): string {
  if (options && options.length > 0) return options.join(", ");
  return text ?? "";
}

export function hasFinal(answer: Answer): boolean {
  return answer.final_answer !== null || answer.final_selected_options !== null;
}

export function hasSuggestion(answer: Answer): boolean {
  return answer.ai_suggestion !== null || answer.ai_selected_options !== null;
}

export function finalText(answer: Answer): string {
  return valueText(answer.final_answer, answer.final_selected_options);
}

export function suggestionText(answer: Answer): string {
  return valueText(answer.ai_suggestion, answer.ai_selected_options);
}

/** The plain-language state of the user's side of a question. */
export const STATUS_LABELS: Record<AnswerStatus, string> = {
  NOT_STARTED: "Not answered",
  NEEDS_INPUT: "Needs your input",
  SUGGESTED: "AI suggestion",
  ACCEPTED: "Accepted",
  USER_EDITED: "Edited by you",
  REJECTED: "Rejected",
};

/** What kind of suggestion it is: built from the application's data, or AI-written. */
export function suggestionLabel(answer: Answer): string {
  return answer.suggestion_method === "DETERMINISTIC"
    ? "Suggestion from your application data"
    : "AI suggestion";
}

/** Whether the user's answer would still satisfy the question (the server decides; this is for the counter). */
export function remainingCharacters(question: Question, text: string): number | null {
  return question.max_length === null ? null : question.max_length - text.length;
}

/** Copy to the clipboard; false when the browser refuses. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      return ok;
    } catch {
      return false;
    }
  }
}

export interface QuestionProblem {
  /** What the user should do about it. */
  kind: "blocked" | "rejected" | "failed" | "invalid" | "conflict";
  code: string;
  message: string;
  violations: DocumentViolation[];
}

export function problemFromError(error: unknown): QuestionProblem {
  if (!(error instanceof ApiError)) {
    return {
      kind: "failed",
      code: "UNKNOWN",
      message: "Something went wrong. Please try again.",
      violations: [],
    };
  }
  const raw = error.details?.violations;
  const violations = Array.isArray(raw) ? (raw as DocumentViolation[]) : [];
  if (error.code === "ANSWER_REJECTED") {
    return { kind: "rejected", code: error.code, message: error.message, violations };
  }
  if (error.status === 422) {
    return { kind: "invalid", code: error.code, message: error.message, violations };
  }
  if (error.status === 409) {
    return { kind: "blocked", code: error.code, message: error.message, violations };
  }
  return { kind: "failed", code: error.code, message: error.message, violations };
}

export function describeViolation(violation: DocumentViolation): string {
  return `${violationLabel(violation)}${violation.excerpt ? `: “${violation.excerpt}”` : ""}`;
}
