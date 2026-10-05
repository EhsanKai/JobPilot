export type EmploymentType =
  | "full_time"
  | "part_time"
  | "contract"
  | "internship"
  | "temporary"
  | "freelance"
  | "working_student";

export type RemotePreference = "onsite" | "remote" | "hybrid" | "flexible";

export type TravelWillingness = "none" | "occasional" | "frequent" | "flexible";

export type RelocationPreference = "yes" | "no" | "maybe";

export type JobTitlePriority = "primary" | "secondary";

export type DealBreakerType =
  | "minimum_salary"
  | "remote_requirement"
  | "employment_type"
  | "location"
  | "relocation"
  | "travel";

export interface DesiredJobTitle {
  id: string;
  title: string;
  priority: JobTitlePriority;
}

export interface DesiredJobTitleInput {
  title: string;
  priority?: JobTitlePriority;
}

export interface PreferredLocation {
  id: string;
  location: string;
  radius_km: number | null;
}

export interface PreferredLocationInput {
  location: string;
  radius_km?: number | null;
}

export interface DealBreaker {
  id: string;
  type: DealBreakerType;
  value: string | null;
  enabled: boolean;
}

export interface DealBreakerInput {
  type: DealBreakerType;
  value?: string | null;
  enabled?: boolean;
}

export interface JobPreferences {
  id: string;
  user_id: string;
  desired_job_titles: DesiredJobTitle[];
  preferred_locations: PreferredLocation[];
  employment_types: EmploymentType[];
  preferred_industries: string[];
  work_models: RemotePreference[];
  minimum_salary: number | null;
  desired_salary: number | null;
  salary_currency: string | null;
  relocation: RelocationPreference | null;
  travel_willingness: TravelWillingness | null;
  deal_breakers: DealBreaker[];
  created_at: string;
  updated_at: string;
}

export interface JobPreferencesInput {
  desired_job_titles?: DesiredJobTitleInput[];
  preferred_locations?: PreferredLocationInput[];
  employment_types?: EmploymentType[];
  preferred_industries?: string[];
  work_models?: RemotePreference[];
  minimum_salary?: number | null;
  desired_salary?: number | null;
  salary_currency?: string | null;
  relocation?: RelocationPreference | null;
  travel_willingness?: TravelWillingness | null;
  deal_breakers?: DealBreakerInput[];
}
