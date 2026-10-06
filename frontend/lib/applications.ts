import type { ApiError } from "@/lib/http";
import type {
  Application,
  ApplicationLanguage,
  ApplicationStatus,
  ApplicationUpdateRequest,
  NoticePeriodUnit,
  PreparationKey,
  WorkAuthorization,
} from "@/types/application";

export const APPLICATION_STATUS_LABELS: Record<ApplicationStatus, string> = {
  preparing: "In preparation",
  // V0 placeholders for the later lifecycle (V0.12); V0.7 never sets them.
  draft: "Draft",
  in_progress: "In progress",
  submitted: "Submitted",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export const APPLICATION_LANGUAGE_OPTIONS: { value: ApplicationLanguage; label: string }[] = [
  { value: "de", label: "German" },
  { value: "en", label: "English" },
];

export const NOTICE_UNIT_OPTIONS: { value: NoticePeriodUnit; label: string }[] = [
  { value: "days", label: "Days" },
  { value: "weeks", label: "Weeks" },
  { value: "months", label: "Months" },
];

export const WORK_AUTHORIZATION_OPTIONS: { value: WorkAuthorization; label: string }[] = [
  { value: "authorized", label: "I am authorized to work there" },
  { value: "requires_sponsorship", label: "I would need sponsorship" },
  { value: "unknown", label: "Not sure yet" },
];

export const PREPARATION_LABELS: Record<PreparationKey, string> = {
  job: "Job selected",
  resume: "Resume selected",
  language: "Application language selected",
  salary: "Salary expectation",
  availability: "Availability (start date)",
  notice_period: "Notice period",
  work_authorization: "Work authorization (optional)",
};

// --- The form -------------------------------------------------------------------------------

export type MentionSalary = "" | "yes" | "no";

/** What the workspace form holds: everything as the user typed it. */
export interface ApplicationForm {
  salary: string;
  currency: string;
  mentionSalary: MentionSalary;
  availableFrom: string;
  noticeValue: string;
  noticeUnit: NoticePeriodUnit;
  workAuthorization: "" | WorkAuthorization;
  language: "" | ApplicationLanguage;
  notes: string;
  resumeId: string;
}

export type FormField = keyof ApplicationForm;
export type FormErrors = Partial<Record<FormField, string>>;

/** DOM id of each form control (so the first invalid one can be focused). */
export const FIELD_IDS: Record<FormField, string> = {
  salary: "salary",
  currency: "currency",
  mentionSalary: "mention_salary",
  availableFrom: "available_from",
  noticeValue: "notice_value",
  noticeUnit: "notice_unit",
  workAuthorization: "work_authorization",
  language: "application_language",
  notes: "notes",
  resumeId: "resume_id",
};

/** The fields in the order they appear on the page. */
const FIELD_ORDER: FormField[] = [
  "salary",
  "currency",
  "mentionSalary",
  "availableFrom",
  "noticeValue",
  "noticeUnit",
  "language",
  "workAuthorization",
  "resumeId",
  "notes",
];

/** The first field (in page order) that has an error, if any. */
export function firstInvalidField(errors: FormErrors): FormField | null {
  return FIELD_ORDER.find((field) => Boolean(errors[field])) ?? null;
}

/** "78000.00" -> "78000", "78000.50" -> "78000.5". */
export function trimDecimal(value: string | null): string {
  if (value === null || value === "") return "";
  const amount = Number(value);
  return Number.isFinite(amount) ? String(amount) : value;
}

export function formFromApplication(application: Application): ApplicationForm {
  return {
    salary: trimDecimal(application.salary_expectation),
    currency: application.salary_currency ?? "",
    mentionSalary:
      application.mention_salary_in_letter === null
        ? ""
        : application.mention_salary_in_letter
          ? "yes"
          : "no",
    availableFrom: application.available_from ?? "",
    noticeValue: application.notice_period ? String(application.notice_period.value) : "",
    noticeUnit: application.notice_period?.unit ?? "months",
    workAuthorization: application.work_authorization ?? "",
    language: application.application_language ?? "",
    notes: application.notes ?? "",
    resumeId: application.resume?.id ?? "",
  };
}

/** The same checks the API makes, so mistakes show up before a request is sent. */
export function validateForm(form: ApplicationForm): FormErrors {
  const errors: FormErrors = {};
  const salary = form.salary.trim();
  const currency = form.currency.trim();

  if (salary !== "") {
    const amount = Number(salary);
    if (!Number.isFinite(amount) || amount <= 0) {
      errors.salary = "Enter an amount greater than 0.";
    } else if (!/^\d+(\.\d{1,2})?$/.test(salary)) {
      errors.salary = "Use at most 2 decimal places.";
    } else if (amount > 10_000_000) {
      errors.salary = "That amount is too large.";
    } else if (currency === "") {
      errors.currency = "Enter the currency (for example EUR).";
    }
  }
  if (currency !== "" && !/^[A-Za-z]{3}$/.test(currency)) {
    errors.currency = "Use a 3-letter currency code, for example EUR.";
  }

  const notice = form.noticeValue.trim();
  if (notice !== "" && !/^\d+$/.test(notice)) {
    errors.noticeValue = "Enter a whole number of 0 or more.";
  }
  return errors;
}

/**
 * The fields that differ from the saved application, in the shape of
 * `PATCH /applications/{id}`: omitted = unchanged, null = cleared. Sending
 * only changes keeps a save from overwriting anything the user didn't touch.
 */
export function buildPatch(saved: Application, form: ApplicationForm): ApplicationUpdateRequest {
  const initial = formFromApplication(saved);
  const patch: ApplicationUpdateRequest = {};

  if (form.salary.trim() !== initial.salary) {
    patch.salary_expectation = form.salary.trim() === "" ? null : form.salary.trim();
  }
  if (form.currency.trim().toUpperCase() !== initial.currency) {
    patch.salary_currency = form.currency.trim() === "" ? null : form.currency.trim().toUpperCase();
  }
  if (form.mentionSalary !== initial.mentionSalary) {
    patch.mention_salary_in_letter =
      form.mentionSalary === "" ? null : form.mentionSalary === "yes";
  }
  if (form.availableFrom !== initial.availableFrom) {
    patch.available_from = form.availableFrom === "" ? null : form.availableFrom;
  }
  const noticeChanged =
    form.noticeValue.trim() !== initial.noticeValue ||
    (form.noticeValue.trim() !== "" && form.noticeUnit !== initial.noticeUnit);
  if (noticeChanged) {
    patch.notice_period =
      form.noticeValue.trim() === ""
        ? null
        : { value: Number(form.noticeValue.trim()), unit: form.noticeUnit };
  }
  if (form.workAuthorization !== initial.workAuthorization) {
    patch.work_authorization = form.workAuthorization === "" ? null : form.workAuthorization;
  }
  if (form.language !== initial.language) {
    patch.application_language = form.language === "" ? null : form.language;
  }
  if (form.notes.trim() !== initial.notes.trim()) {
    patch.notes = form.notes.trim() === "" ? null : form.notes;
  }
  if (form.resumeId !== "" && form.resumeId !== initial.resumeId) {
    patch.resume_id = form.resumeId;
  }
  return patch;
}

export function hasChanges(patch: ApplicationUpdateRequest): boolean {
  return Object.keys(patch).length > 0;
}

// --- Server errors ----------------------------------------------------------------------------

const API_FIELD_TO_FORM_FIELD: Record<string, FormField> = {
  salary_expectation: "salary",
  salary_currency: "currency",
  mention_salary_in_letter: "mentionSalary",
  available_from: "availableFrom",
  notice_period: "noticeValue",
  work_authorization: "workAuthorization",
  application_language: "language",
  notes: "notes",
  resume_id: "resumeId",
};

interface PydanticIssue {
  loc?: unknown;
  msg?: unknown;
}

/**
 * Maps an API validation error to the form fields it concerns. Service-level
 * errors carry `details.field`; request validation errors carry
 * `details.errors[].loc` (["body", "<field>", ...]). Errors that name no known
 * field are returned as `general`.
 */
export function fieldErrorsFromApi(error: ApiError): { fields: FormErrors; general: string } {
  const fields: FormErrors = {};
  const named = error.details?.field;
  if (typeof named === "string" && API_FIELD_TO_FORM_FIELD[named]) {
    fields[API_FIELD_TO_FORM_FIELD[named]] = error.message;
  }
  const issues = error.details?.errors;
  if (Array.isArray(issues)) {
    for (const issue of issues as PydanticIssue[]) {
      const loc = Array.isArray(issue.loc) ? issue.loc : [];
      const name = typeof loc[1] === "string" ? loc[1] : null;
      const field = name ? API_FIELD_TO_FORM_FIELD[name] : undefined;
      if (field && typeof issue.msg === "string" && !fields[field]) {
        fields[field] = issue.msg.replace(/^Value error, /, "");
      }
    }
  }
  return { fields, general: error.message };
}
