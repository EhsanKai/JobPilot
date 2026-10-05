export type EmploymentType =
  | "full_time"
  | "part_time"
  | "contract"
  | "internship"
  | "temporary"
  | "freelance";

export type SeniorityLevel =
  | "intern"
  | "junior"
  | "mid"
  | "senior"
  | "lead"
  | "principal"
  | "executive";

export type RemotePreference = "onsite" | "remote" | "hybrid" | "flexible";

export type TravelWillingness = "none" | "occasional" | "frequent";

export interface LanguagePreference {
  language: string;
  proficiency: string | null;
}

export interface JobPreferences {
  id: string;
  user_id: string;
  desired_job_titles: string[];
  preferred_locations: string[];
  employment_types: EmploymentType[];
  industries: string[];
  languages: LanguagePreference[];
  minimum_salary: number | null;
  desired_salary: number | null;
  salary_currency: string | null;
  seniority: SeniorityLevel | null;
  remote_preference: RemotePreference | null;
  relocation_willingness: boolean;
  travel_willingness: TravelWillingness | null;
  created_at: string;
  updated_at: string;
}

export type JobPreferencesInput = Omit<
  JobPreferences,
  "id" | "user_id" | "created_at" | "updated_at"
>;
