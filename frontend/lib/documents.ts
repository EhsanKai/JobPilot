import type { ApiError } from "@/lib/http";
import type {
  DocumentSummary,
  DocumentType,
  DocumentViolation,
} from "@/types/documents";

export const DOCUMENT_TYPES: DocumentType[] = ["tailored_cv", "cover_letter"];

export const DOCUMENT_TITLES: Record<DocumentType, string> = {
  tailored_cv: "Tailored CV",
  cover_letter: "Cover letter (Anschreiben)",
};

export const GENERATE_LABELS: Record<DocumentType, string> = {
  tailored_cv: "Generate CV",
  cover_letter: "Generate Anschreiben",
};

export const LANGUAGE_NAMES: Record<string, string> = { de: "German", en: "English" };

export function languageName(code: string | null | undefined): string | null {
  if (!code) return null;
  return LANGUAGE_NAMES[code] ?? code.toUpperCase();
}

/** Plain-language names for the reasons a generated document is rejected. */
const VIOLATION_LABELS: Record<string, string> = {
  UNSUPPORTED_SKILL: "A skill, language or degree your resume doesn't state",
  UNSUPPORTED_LANGUAGE: "A language your resume doesn't state",
  UNSUPPORTED_DEGREE: "A degree your resume doesn't state",
  UNSUPPORTED_TECHNOLOGY: "A tool or technology your resume doesn't state",
  UNSUPPORTED_ORGANIZATION: "An employer or organization your resume doesn't state",
  UNSUPPORTED_NAME: "A name your resume doesn't contain",
  UNSUPPORTED_NUMBER: "A number your resume doesn't state",
  UNSUPPORTED_ROLE_CLAIM: "A role, level or responsibility your resume doesn't state",
  UNSUPPORTED_CREDENTIAL: "A certification or degree your resume doesn't state",
  UNSUPPORTED_ACHIEVEMENT: "An achievement your resume doesn't state",
  UNSUPPORTED_EXPERIENCE_CLAIM: "An amount of experience your resume doesn't state",
  COMPANY_KNOWLEDGE_CLAIM: "A claim about the company that no fact supports",
  PERSONAL_CIRCUMSTANCE_CLAIM: "A personal circumstance that no fact supports",
  SALARY_MENTION_NOT_ALLOWED: "A salary mention you didn't ask for",
  WRONG_LANGUAGE: "Not written in the application language",
  MISSING_SOURCE: "Text without a source in your resume",
  UNKNOWN_FACT_ID: "A source that doesn't exist",
  FOREIGN_FACT: "A source from a different role",
  JOB_FACT_AS_SOURCE: "A job requirement used as proof about you",
  UNKNOWN_EXPERIENCE: "A role that isn't in your resume",
  DUPLICATE_EXPERIENCE: "A role listed twice",
  MISSING_EXPERIENCE: "A role from your resume left out",
  UNKNOWN_SKILL: "A skill that isn't in your resume",
  DUPLICATE_SKILL: "A skill listed twice",
  INVALID_STRUCTURE: "A letter without the expected structure",
  EMPTY_TEXT: "Empty text",
  // V0.9: Humanize and application questions
  FACT_DROPPED: "Something from the original letter was left out",
  IDENTITY_NOT_PRESERVED: "The position or company named in the original was left out",
  UNCHANGED: "Nothing was reworded",
  ANSWER_TOO_LONG: "An answer longer than the character limit",
  INVALID_OPTION: "An option the question doesn't list",
  INVALID_ANSWER_FORMAT: "An answer in the wrong format",
  UNSUPPORTED_TOPIC: "Facts that aren't about what the question asks",
  UNSUPPORTED_NEGATIVE: "A \"No\" that no fact supports",
};

export function violationLabel(violation: DocumentViolation): string {
  return VIOLATION_LABELS[violation.code] ?? "Something your resume doesn't support";
}

/** The newest validated document of a type (the server marks it `is_current`). */
export function currentDocument(
  items: DocumentSummary[],
  type: DocumentType
): DocumentSummary | null {
  return items.find((item) => item.document_type === type && item.is_current) ?? null;
}

/** Every attempt of a type, newest first (the order the API returns). */
export function attemptsOf(items: DocumentSummary[], type: DocumentType): DocumentSummary[] {
  return items.filter((item) => item.document_type === type);
}

/**
 * The newest attempt of a type, when it did not produce a document and is
 * newer than the current one: what the user should hear about ("rejected",
 * "failed", "still generating").
 */
export function pendingAttempt(items: DocumentSummary[], type: DocumentType): DocumentSummary | null {
  const attempts = attemptsOf(items, type);
  const newest = attempts[0];
  if (!newest || newest.status === "validated") return null;
  return newest;
}

export function isGenerating(items: DocumentSummary[], type?: DocumentType): boolean {
  return items.some(
    (item) => item.status === "generating" && (type === undefined || item.document_type === type)
  );
}

/** "today", "yesterday" or a date, for when a document was generated. */
export function describeGenerated(value: string | null, now: Date = new Date()): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(now) - day(date)) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** "Jan 2023 – present" from the resume's own dates, falling back to the text as written. */
export function describeDates(entry: {
  start_date: string | null;
  end_date: string | null;
  is_current?: boolean | null;
  date_text: string | null;
}): string | null {
  if (entry.start_date) {
    const end = entry.end_date ?? (entry.is_current ? "present" : null);
    return end ? `${entry.start_date} – ${end}` : entry.start_date;
  }
  return entry.date_text ?? entry.end_date;
}

/** What a failed generation request tells the user. */
export interface GenerationProblem {
  kind: "rejected" | "blocked" | "failed";
  message: string;
  violations: DocumentViolation[];
}

export function problemFromApi(error: ApiError): GenerationProblem {
  if (error.code === "DOCUMENT_REJECTED") {
    const raw = error.details?.violations;
    return {
      kind: "rejected",
      message: error.message,
      violations: Array.isArray(raw) ? (raw as DocumentViolation[]) : [],
    };
  }
  if (error.status === 409) {
    return { kind: "blocked", message: error.message, violations: [] };
  }
  return { kind: "failed", message: error.message, violations: [] };
}
