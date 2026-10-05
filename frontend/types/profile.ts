export interface UserProfile {
  id: string;
  user_id: string;
  phone: string | null;
  location: string | null;
  linkedin_url: string | null;
  current_position: string | null;
  years_of_experience: number | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileWithUser {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  profile: UserProfile | null;
}

export interface ProfileUpdateInput {
  first_name?: string;
  last_name?: string;
  phone?: string | null;
  location?: string | null;
  linkedin_url?: string | null;
  current_position?: string | null;
  years_of_experience?: number | null;
}
