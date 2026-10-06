import type {
  CoverLetterContent,
  DocumentList,
  DocumentReadiness,
  DocumentSummary,
  DocumentType,
  GeneratedDocument,
  TailoredCvContent,
} from "@/types/documents";

export const READY: DocumentReadiness = { can_generate: true, blockers: [] };

export function blocked(...blockers: [string, string][]): DocumentReadiness {
  return {
    can_generate: false,
    blockers: blockers.map(([code, message]) => ({ code, message })),
  };
}

let counter = 0;

/** One generation attempt; a validated, current document unless overridden. */
export function makeSummary(overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  counter += 1;
  const status = overrides.status ?? "validated";
  return {
    id: `doc-${counter}`,
    application_id: "app-1",
    document_type: "tailored_cv",
    status,
    version_number: status === "validated" ? 1 : null,
    is_current: status === "validated",
    is_outdated: false,
    language: "de",
    resume_id: "resume-2",
    resume_version_number: 2,
    application_context_snapshot_id: "snapshot-1",
    input_hash: "b".repeat(64),
    prompt_version: "tailored_cv_v1",
    ai_provider: "anthropic",
    ai_model: "claude-sonnet-5-5",
    validation_status: status === "validated" ? "passed" : status === "rejected" ? "failed" : "not_run",
    violation_count: status === "rejected" ? 2 : 0,
    error_code: null,
    error_message: null,
    created_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeCvContent(overrides: Partial<TailoredCvContent> = {}): TailoredCvContent {
  return {
    kind: "tailored_cv",
    schema_version: "tailored_cv_content_v1",
    language: "de",
    header: {
      full_name: "Alex Example",
      email: "alex.example@example.com",
      phone: "+49 170 1234567",
      location: "Berlin, Germany",
      links: ["https://linkedin.com/in/alex-example-test"],
    },
    summary: {
      text: "SAP-Entwickler mit Erfahrung in SAP IS-U und ABAP.",
      source_fact_ids: ["summary"],
    },
    experience: [
      {
        source_fact_id: "exp1",
        company: "Example Energy GmbH",
        job_title: "SAP IS-U Developer",
        location: "Berlin",
        start_date: "2023-01",
        end_date: null,
        is_current: true,
        date_text: "01/2023 - present",
        bullets: [
          {
            text: "Entwicklung und Wartung von SAP IS-U-Anwendungen",
            source_fact_ids: ["exp1.r1"],
          },
        ],
      },
    ],
    education: [
      {
        source_fact_id: "edu1",
        institution: "Technische Universität Example",
        degree: "M.Sc.",
        field_of_study: "Wirtschaftsinformatik",
        location: null,
        start_date: "2016",
        end_date: "2019",
        date_text: "2016 - 2019",
      },
    ],
    skills: [
      { source_fact_id: "skill1", name: "SAP ABAP", emphasized: true },
      { source_fact_id: "skill2", name: "Git", emphasized: false },
    ],
    languages: [{ source_fact_id: "lang1", language: "German", proficiency: "native", cefr_level: null }],
    certifications: [
      { source_fact_id: "cert1", name: "SAP Certified Development Associate", issuer: "SAP", date: "2020" },
    ],
    projects: [],
    additional_information: [],
    ...overrides,
  };
}

export function makeLetterContent(overrides: Partial<CoverLetterContent> = {}): CoverLetterContent {
  return {
    kind: "cover_letter",
    schema_version: "cover_letter_content_v1",
    language: "de",
    sender: {
      full_name: "Alex Example",
      email: "alex.example@example.com",
      phone: null,
      location: "Berlin, Germany",
    },
    recipient: { company_name: "Acme GmbH", job_title: "Senior Backend Engineer (Python)" },
    subject: "Bewerbung als Senior Backend Engineer (Python)",
    salutation: "Sehr geehrte Damen und Herren,",
    paragraphs: [
      {
        role: "opening",
        text: "mit großem Interesse bewerbe ich mich auf die Position.",
        source_fact_ids: [],
        job_fact_ids: ["job.title"],
      },
      {
        role: "experience",
        text: "In meiner Tätigkeit habe ich SAP IS-U Anwendungen entwickelt.",
        source_fact_ids: ["exp1.r1"],
        job_fact_ids: [],
      },
      {
        role: "closing",
        text: "Ich freue mich auf ein persönliches Gespräch.",
        source_fact_ids: [],
        job_fact_ids: [],
      },
    ],
    salary_statement: "Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr.",
    closing_formula: "Mit freundlichen Grüßen",
    signature: "Alex Example",
    ...overrides,
  };
}

/** The full document of a summary (the detail endpoint). */
export function makeDocument(
  overrides: Partial<GeneratedDocument> = {},
  type: DocumentType = "tailored_cv"
): GeneratedDocument {
  const summary = makeSummary({
    document_type: type,
    prompt_version: type === "tailored_cv" ? "tailored_cv_v1" : "cover_letter_v1",
    ...overrides,
  });
  return {
    ...summary,
    content: type === "tailored_cv" ? makeCvContent() : makeLetterContent(),
    validation:
      summary.status === "validated"
        ? { schema_version: "document_validation_v1", status: "passed", text_units_checked: 3, violations: [] }
        : null,
    source_facts: {
      summary: { text: "SAP developer with experience in SAP IS-U and ABAP.", ref: "resume_profile:p/1" },
      "exp1.r1": {
        text: "Developed and maintained SAP IS-U applications",
        ref: "resume_profile:p/2",
      },
    },
    ...overrides,
  } as GeneratedDocument;
}

export function makeList(
  items: DocumentSummary[] = [],
  readiness: Partial<Record<DocumentType, DocumentReadiness>> = {}
): DocumentList {
  return {
    items,
    tailored_cv: readiness.tailored_cv ?? READY,
    cover_letter: readiness.cover_letter ?? READY,
  };
}
