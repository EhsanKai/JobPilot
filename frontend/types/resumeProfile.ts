import type { Resume } from "@/types/resume";

// Mirrors backend/app/schemas/resume_profile.py.

export type Confidence = "high" | "medium" | "low";
export type Basis = "explicit" | "inferred";
export type ReviewStatus = "pending" | "accepted" | "edited" | "rejected";
export type SkillProficiencyLevel = "beginner" | "intermediate" | "advanced" | "expert" | "unknown";
export type SkillCategory =
  | "programming_language"
  | "framework_or_library"
  | "platform"
  | "tool"
  | "database"
  | "cloud"
  | "methodology"
  | "domain_knowledge"
  | "soft_skill"
  | "other";
export type CefrLevel = "A1" | "A2" | "B1" | "B2" | "C1" | "C2";
export type PersonalField =
  | "full_name"
  | "email"
  | "phone"
  | "location"
  | "linkedin_url"
  | "github_url"
  | "portfolio_url";
export type AdditionalCategory =
  | "award"
  | "publication"
  | "volunteering"
  | "interest"
  | "driving_license"
  | "work_authorization"
  | "availability"
  | "other";

export interface Evidence {
  source_section: string | null;
  source_text: string;
  verified: boolean;
}

export interface ReviewFields {
  id: string;
  review_status: ReviewStatus;
  user_edited_fields: string[];
  confidence: Confidence;
  basis: Basis;
  evidence: Evidence[];
}

export interface PersonalInfoItem extends ReviewFields {
  field: PersonalField;
  value: string;
}

export interface SummaryItem extends ReviewFields {
  value: string;
}

interface Dated {
  start_date: string | null;
  end_date: string | null;
  date_text: string | null;
}

export interface WorkExperienceItem extends ReviewFields, Dated {
  company: string | null;
  job_title: string | null;
  location: string | null;
  is_current: boolean | null;
  description: string | null;
  responsibilities: string[];
  technologies: string[];
  achievements: string[];
}

export interface EducationItem extends ReviewFields, Dated {
  institution: string | null;
  degree: string | null;
  field_of_study: string | null;
  location: string | null;
  description: string | null;
}

export interface SkillItem extends ReviewFields {
  name: string;
  aliases: string[];
  category: SkillCategory | null;
  proficiency: SkillProficiencyLevel;
  proficiency_evidence: Evidence | null;
}

export interface LanguageItem extends ReviewFields {
  language: string;
  as_written: string | null;
  proficiency: string | null;
  cefr_level: CefrLevel | null;
}

export interface CertificationItem extends ReviewFields {
  name: string;
  issuer: string | null;
  date: string | null;
  credential_id: string | null;
  url: string | null;
}

export interface ProjectItem extends ReviewFields, Dated {
  name: string;
  description: string | null;
  role: string | null;
  technologies: string[];
  url: string | null;
}

export interface AdditionalInfoItem extends ReviewFields {
  category: AdditionalCategory;
  value: string;
}

export interface ResumeProfileData {
  schema_version: number;
  document_language: string | null;
  personal_information: PersonalInfoItem[];
  summary: SummaryItem | null;
  work_experience: WorkExperienceItem[];
  education: EducationItem[];
  skills: SkillItem[];
  languages: LanguageItem[];
  certifications: CertificationItem[];
  projects: ProjectItem[];
  additional_information: AdditionalInfoItem[];
}

export type SectionName = Exclude<keyof ResumeProfileData, "schema_version" | "document_language">;

export type ReviewableItem =
  | PersonalInfoItem
  | SummaryItem
  | WorkExperienceItem
  | EducationItem
  | SkillItem
  | LanguageItem
  | CertificationItem
  | ProjectItem
  | AdditionalInfoItem;

export interface ProfileWarning {
  code: string;
  message: string;
  section: SectionName | null;
  item_id: string | null;
}

export interface SectionCounts {
  total: number;
  pending: number;
  accepted: number;
  edited: number;
  rejected: number;
}

export interface ProfileSummary {
  sections: Record<SectionName, SectionCounts>;
  total_items: number;
  pending_items: number;
}

export interface ProfileConflict {
  field: string;
  label: string;
  profile_value: string;
  resume_value: string;
  potential_conflict: boolean;
  message: string;
}

export interface DerivedMetrics {
  total_experience_months: number | null;
  experience_years: number | null;
  experience_is_partial: boolean;
  current_job_title: string | null;
}

export type ResumeProfileStatus =
  | "processing"
  | "needs_review"
  | "confirmed"
  | "superseded"
  | "failed"
  | "ocr_required";

export interface ResumeProfile {
  id: string;
  resume_id: string;
  status: ResumeProfileStatus;
  parser_version: string;
  schema_version: number;
  ai_provider: string | null;
  ai_model: string | null;
  source_file_hash: string | null;
  reused_from_profile_id: string | null;
  extraction_method: string | null;
  page_count: number | null;
  created_at: string;
  processed_at: string | null;
  confirmed_at: string | null;
  data: ResumeProfileData;
  raw_data: ResumeProfileData;
  warnings: ProfileWarning[];
  summary: ProfileSummary;
  conflicts: ProfileConflict[];
  derived: DerivedMetrics;
}

export interface ProcessingAttempt {
  profile_id: string;
  status: ResumeProfileStatus;
  error_code: string | null;
  error_message: string | null;
  parser_version: string;
  started_at: string | null;
  processed_at: string | null;
}

export interface ResumeIntelligence {
  resume: Resume;
  latest_attempt: ProcessingAttempt | null;
  is_processing: boolean;
  profile: ResumeProfile | null;
}

export interface ResumeProcessResponse {
  resume: Resume;
  profile_id: string;
  reused: boolean;
  message: string;
}

export interface ResumeSource {
  profile_id: string;
  extraction_method: string | null;
  page_count: number | null;
  text: string;
}

export type ReviewAction = "accept" | "reject" | "edit" | "reset";

export interface ReviewItemRequest {
  action: ReviewAction;
  changes?: Record<string, unknown>;
}
