import type { ResumeStatus } from "@/types/resume";
import type {
  AdditionalCategory,
  Confidence,
  PersonalField,
  ProfileSummary,
  SectionName,
  SkillCategory,
} from "@/types/resumeProfile";

export const IN_PROGRESS_STATUSES: ResumeStatus[] = ["extracting", "extracted", "parsing", "parsed"];

export function isInProgress(status: ResumeStatus): boolean {
  return IN_PROGRESS_STATUSES.includes(status);
}

export function progressLabel(status: ResumeStatus): string {
  if (status === "extracting" || status === "extracted") return "Extracting text from your resume...";
  return "Reading your resume and structuring the information...";
}

export const SECTION_LABELS: Record<SectionName, string> = {
  personal_information: "Personal Information",
  summary: "Professional Summary",
  work_experience: "Work Experience",
  skills: "Skills",
  education: "Education",
  languages: "Languages",
  certifications: "Certifications",
  projects: "Projects",
  additional_information: "Additional Information",
};

// Display order of the review sections.
export const SECTION_ORDER: SectionName[] = [
  "personal_information",
  "summary",
  "work_experience",
  "skills",
  "education",
  "languages",
  "certifications",
  "projects",
  "additional_information",
];

const COUNT_NOUNS: Partial<Record<SectionName, [string, string]>> = {
  skills: ["skill", "skills"],
  work_experience: ["work experience", "work experiences"],
  education: ["education entry", "education entries"],
  languages: ["language", "languages"],
  certifications: ["certification", "certifications"],
  projects: ["project", "projects"],
};

/** "23 skills detected"-style lines for the sections that have content. */
export function detectedCountLines(summary: ProfileSummary): string[] {
  return (Object.keys(COUNT_NOUNS) as SectionName[])
    .map((section) => {
      const total = summary.sections[section]?.total ?? 0;
      const [singular, plural] = COUNT_NOUNS[section]!;
      return total > 0 ? `${total} ${total === 1 ? singular : plural} detected` : null;
    })
    .filter((line): line is string => line !== null);
}

export const CONFIDENCE_LABELS: Record<Confidence, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

export const PERSONAL_FIELD_LABELS: Record<PersonalField, string> = {
  full_name: "Full name",
  email: "Email",
  phone: "Phone",
  location: "Location",
  linkedin_url: "LinkedIn",
  github_url: "GitHub",
  portfolio_url: "Portfolio",
};

export const SKILL_CATEGORY_LABELS: Record<SkillCategory, string> = {
  programming_language: "Programming language",
  framework_or_library: "Framework / library",
  platform: "Platform",
  tool: "Tool",
  database: "Database",
  cloud: "Cloud",
  methodology: "Methodology",
  domain_knowledge: "Domain knowledge",
  soft_skill: "Soft skill",
  other: "Other",
};

export const ADDITIONAL_CATEGORY_LABELS: Record<AdditionalCategory, string> = {
  award: "Award",
  publication: "Publication",
  volunteering: "Volunteering",
  interest: "Interest",
  driving_license: "Driving license",
  work_authorization: "Work authorization",
  availability: "Availability",
  other: "Other",
};

export function formatDateRange(
  start: string | null,
  end: string | null,
  isCurrent?: boolean | null
): string | null {
  if (!start && !end && !isCurrent) return null;
  const endLabel = isCurrent ? "Present" : end ?? "unknown";
  return `${start ?? "unknown"} – ${endLabel}`;
}
