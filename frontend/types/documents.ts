/** Tailored documents (V0.8). Mirrors backend/app/schemas/generated_documents.py. */

export type DocumentType = "tailored_cv" | "cover_letter";

/**
 * "generating" only while a request is in flight (the server turns an
 * abandoned one into "failed"). Only "validated" is a usable document.
 */
export type DocumentStatus = "generating" | "validated" | "rejected" | "failed";

/** "not_run": still generating, or failed before validation could run. */
export type ValidationStatus = "passed" | "failed" | "not_run";

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
  summary: { text: string; source_fact_ids: string[] };
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

export interface DocumentList {
  /** Every attempt, newest first. */
  items: DocumentSummary[];
  tailored_cv: DocumentReadiness;
  cover_letter: DocumentReadiness;
}
