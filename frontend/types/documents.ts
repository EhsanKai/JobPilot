/** Tailored documents (V0.8). Mirrors backend/app/schemas/generated_documents.py. */

export type DocumentType = "tailored_cv" | "cover_letter";

/**
 * "generating" only while a request is in flight (the server turns an
 * abandoned one into "failed"). Only "validated" is a usable document.
 */
export type DocumentStatus = "generating" | "validated" | "rejected" | "failed";

/** "not_run": still generating, or failed before validation could run.
 *  "user_authored": the user's own edit: never checked, never shown as checked. */
export type ValidationStatus = "passed" | "failed" | "not_run" | "user_authored";

/** Whose words a version holds: the AI's (checked), a reworded one (checked), or the user's. */
export type DocumentOrigin = "ai" | "humanized" | "user_edited";

/** Whose words a piece of text is. User text cites no facts. */
export type TextOrigin = "ai" | "user";

export interface DocumentBlocker {
  code: string;
  message: string;
}

/** What must be done before the next generation of a type. */
export interface DocumentReadiness {
  can_generate: boolean;
  blockers: DocumentBlocker[];
}

export interface DocumentSummary {
  id: string;
  application_id: string;
  document_type: DocumentType;
  status: DocumentStatus;
  /** Among the validated documents of its type; null for any other attempt. */
  version_number: number | null;
  /** The newest validated document of its type. */
  is_current: boolean;
  /** Current, but its resume / application data / match changed since. */
  is_outdated: boolean;
  language: string;
  resume_id: string | null;
  resume_version_number: number | null;
  application_context_snapshot_id: string;
  input_hash: string;
  prompt_version: string;
  prompt_hash: string | null;
  origin: DocumentOrigin;
  /** The version a humanized / edited one was made from. */
  base_document_id: string | null;
  ai_provider: string;
  ai_model: string | null;
  validation_status: ValidationStatus;
  violation_count: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface DocumentViolation {
  code: string;
  message: string;
  /** Where in the AI output it was found, e.g. "experience[1].bullets[0]". */
  location: string;
  /** The offending words. */
  excerpt: string | null;
}

export interface ValidationReport {
  schema_version: string;
  status: "passed" | "failed";
  text_units_checked: number;
  violations: DocumentViolation[];
}

export interface SourceFact {
  text: string;
  ref: string | null;
}

// --- Tailored CV ---------------------------------------------------------------------------

export interface CvBullet {
  text: string;
  source_fact_ids: string[];
  origin?: TextOrigin;
}

export interface TailoredCvContent {
  kind: "tailored_cv";
  schema_version: string;
  language: string;
  header: {
    full_name: string | null;
    email: string | null;
    phone: string | null;
    location: string | null;
    links: string[];
  };
  summary: { text: string; source_fact_ids: string[]; origin?: TextOrigin };
  experience: {
    source_fact_id: string;
    company: string | null;
    job_title: string | null;
    location: string | null;
    start_date: string | null;
    end_date: string | null;
    is_current: boolean | null;
    date_text: string | null;
    bullets: CvBullet[];
  }[];
  education: {
    source_fact_id: string;
    institution: string | null;
    degree: string | null;
    field_of_study: string | null;
    location: string | null;
    start_date: string | null;
    end_date: string | null;
    date_text: string | null;
  }[];
  skills: { source_fact_id: string; name: string; emphasized: boolean }[];
  languages: {
    source_fact_id: string;
    language: string;
    proficiency: string | null;
    cefr_level: string | null;
  }[];
  certifications: {
    source_fact_id: string;
    name: string;
    issuer: string | null;
    date: string | null;
  }[];
  projects: {
    source_fact_id: string;
    name: string;
    role: string | null;
    description: string | null;
    technologies: string[];
  }[];
  additional_information: { source_fact_id: string; category: string; value: string }[];
}

// --- Cover letter (Anschreiben) -----------------------------------------------------------

export type LetterRole =
  | "opening"
  | "role_fit"
  | "experience"
  | "skills"
  | "motivation"
  | "closing";

export interface CoverLetterContent {
  kind: "cover_letter";
  schema_version: string;
  language: string;
  sender: {
    full_name: string | null;
    email: string | null;
    phone: string | null;
    location: string | null;
  };
  recipient: { company_name: string | null; job_title: string | null };
  subject: string;
  salutation: string;
  paragraphs: {
    role: LetterRole;
    text: string;
    source_fact_ids: string[];
    job_fact_ids: string[];
    origin?: TextOrigin;
  }[];
  /** Only when you chose to mention your salary expectation. */
  salary_statement: string | null;
  closing_formula: string;
  signature: string | null;
}

export interface GeneratedDocument extends DocumentSummary {
  content: TailoredCvContent | CoverLetterContent | null;
  validation: ValidationReport | null;
  /** The facts the content cites, by id. */
  source_facts: Record<string, SourceFact>;
}

export interface CvPhoto {
  content_type: string;
  file_name: string;
  size_bytes: number;
  updated_at: string;
}

export interface DocumentList {
  /** Every attempt, newest first. */
  items: DocumentSummary[];
  tailored_cv: DocumentReadiness;
  cover_letter: DocumentReadiness;
  /** The user's own photo for the CV, if they added one (V0.9). */
  photo?: CvPhoto | null;
}

// --- Edits (V0.9) ---------------------------------------------------------------------------

export interface CvEditRequest {
  kind: "tailored_cv";
  summary: string;
  header: {
    full_name: string | null;
    email: string | null;
    phone: string | null;
    location: string | null;
    links: string[];
  };
  experience: { source_fact_id: string; bullets: string[] }[];
}

export interface LetterEditRequest {
  kind: "cover_letter";
  subject: string;
  salutation: string;
  paragraphs: { role: LetterRole; text: string }[];
  salary_statement: string | null;
  closing_formula: string;
  signature: string | null;
}

export type DocumentEditRequest = CvEditRequest | LetterEditRequest;
