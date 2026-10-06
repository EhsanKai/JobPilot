import type { Resume } from "@/types/resume";
import type {
  ResumeIntelligence,
  ResumeProfile,
  ResumeProfileData,
  SectionName,
} from "@/types/resumeProfile";

export function makeResume(overrides: Partial<Resume> = {}): Resume {
  return {
    id: "resume-1",
    user_id: "user-1",
    version_number: 1,
    file_name: "resume.pdf",
    mime_type: "application/pdf",
    file_size: 2048,
    status: "uploaded",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const evidence = (text: string, section = "Experience", verified = true) => ({
  source_section: section,
  source_text: text,
  verified,
});

const base = { review_status: "pending" as const, user_edited_fields: [], basis: "explicit" as const };

export function makeProfileData(): ResumeProfileData {
  return {
    schema_version: 1,
    document_language: "en",
    personal_information: [
      {
        ...base,
        id: "p-name",
        field: "full_name",
        value: "Alex Example",
        confidence: "high",
        evidence: [evidence("Alex Example", "Header")],
      },
    ],
    summary: null,
    work_experience: [
      {
        ...base,
        id: "w-1",
        company: "Example Energy GmbH",
        job_title: "SAP IS-U Developer",
        location: "Berlin",
        start_date: "2023-01",
        end_date: null,
        date_text: "01/2023 - present",
        is_current: true,
        description: null,
        responsibilities: ["Developed and maintained SAP IS-U applications"],
        technologies: ["SAP IS-U", "S/4HANA"],
        achievements: [],
        confidence: "high",
        evidence: [evidence("Developed and maintained SAP IS-U applications")],
      },
    ],
    education: [],
    skills: [
      {
        ...base,
        id: "s-isu",
        name: "SAP IS-U",
        aliases: [],
        category: "platform",
        proficiency: "unknown",
        proficiency_evidence: null,
        confidence: "high",
        evidence: [evidence("Developed and maintained SAP IS-U applications")],
      },
      {
        ...base,
        id: "s-btp",
        name: "SAP BTP",
        aliases: [],
        category: "platform",
        proficiency: "unknown",
        proficiency_evidence: null,
        confidence: "medium",
        evidence: [evidence("Worked with SAP BTP for side-by-side extensions")],
      },
      {
        ...base,
        id: "s-pm",
        name: "Project Management",
        aliases: [],
        category: "methodology",
        proficiency: "unknown",
        proficiency_evidence: null,
        basis: "inferred",
        confidence: "low",
        evidence: [evidence("Coordinated a team of five", "Experience", false)],
      },
    ],
    languages: [
      {
        ...base,
        id: "l-de",
        language: "German",
        as_written: "German",
        proficiency: "native",
        cefr_level: null,
        confidence: "high",
        evidence: [evidence("German - native", "Languages")],
      },
    ],
    certifications: [],
    projects: [],
    additional_information: [],
  };
}

const SECTIONS: SectionName[] = [
  "personal_information",
  "summary",
  "work_experience",
  "education",
  "skills",
  "languages",
  "certifications",
  "projects",
  "additional_information",
];

export function summarize(data: ResumeProfileData): ResumeProfile["summary"] {
  const sections = {} as ResumeProfile["summary"]["sections"];
  let total = 0;
  let pending = 0;
  for (const section of SECTIONS) {
    const value = data[section];
    const items = value === null ? [] : Array.isArray(value) ? value : [value];
    const counts = { total: 0, pending: 0, accepted: 0, edited: 0, rejected: 0 };
    for (const item of items) {
      counts.total += 1;
      counts[item.review_status] += 1;
    }
    sections[section] = counts;
    total += counts.total;
    pending += counts.pending;
  }
  return { sections, total_items: total, pending_items: pending };
}

export function makeProfile(overrides: Partial<ResumeProfile> = {}): ResumeProfile {
  const data = overrides.data ?? makeProfileData();
  return {
    id: "profile-1",
    resume_id: "resume-1",
    status: "needs_review",
    parser_version: "resume_parser_v1",
    schema_version: 1,
    ai_provider: "anthropic",
    ai_model: "claude-sonnet-5-5",
    source_file_hash: "abc",
    reused_from_profile_id: null,
    extraction_method: "pypdf",
    page_count: 1,
    created_at: "2026-01-01T00:00:00Z",
    processed_at: "2026-01-01T00:00:10Z",
    confirmed_at: null,
    raw_data: makeProfileData(),
    warnings: [],
    conflicts: [],
    derived: {
      total_experience_months: 40,
      experience_years: 3.3,
      experience_is_partial: false,
      current_job_title: "SAP IS-U Developer",
    },
    ...overrides,
    data,
    summary: overrides.summary ?? summarize(data),
  };
}

export function makeIntelligence(overrides: Partial<ResumeIntelligence> = {}): ResumeIntelligence {
  return {
    resume: makeResume({ status: "needs_review" }),
    latest_attempt: null,
    is_processing: false,
    profile: makeProfile(),
    ...overrides,
  };
}
