export type SeniorityLevel =
  | "intern"
  | "junior"
  | "mid"
  | "senior"
  | "lead"
  | "manager"
  | "director";

export interface ProfileWithUser {
  id: string;
  email: string;
  first_name: string;
  last_name: string;

  // Personal profile
  phone: string | null;
  location: string | null;
  linkedin_url: string | null;

  // Professional profile
  current_job_title: string | null;
  years_of_experience: number | null;
  seniority_level: SeniorityLevel | null;
  industries: string[];
}

export interface ProfileUpdateInput {
  first_name?: string;
  last_name?: string;
  phone?: string | null;
  location?: string | null;
  linkedin_url?: string | null;
  current_job_title?: string | null;
  years_of_experience?: number | null;
  seniority_level?: SeniorityLevel | null;
  industries?: string[];
}
