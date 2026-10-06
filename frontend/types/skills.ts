export type SkillProficiency = "beginner" | "intermediate" | "advanced" | "expert";

export interface Skill {
  id: string;
  name: string;
  proficiency: SkillProficiency | null;
  years_of_experience: number | null;
}

export interface SkillCreateInput {
  name: string;
  proficiency?: SkillProficiency | null;
  years_of_experience?: number | null;
}

export interface SkillUpdateInput {
  proficiency?: SkillProficiency | null;
  years_of_experience?: number | null;
}
