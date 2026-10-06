import { api } from "@/lib/http";
import type { Skill, SkillCreateInput, SkillUpdateInput } from "@/types/skills";

export function getSkills(): Promise<Skill[]> {
  return api.get<Skill[]>("/profile/skills");
}

export function addSkill(input: SkillCreateInput): Promise<Skill> {
  return api.post<Skill>("/profile/skills", input);
}

export function updateSkill(skillId: string, input: SkillUpdateInput): Promise<Skill> {
  return api.put<Skill>(`/profile/skills/${skillId}`, input);
}

export function deleteSkill(skillId: string): Promise<void> {
  return api.delete<void>(`/profile/skills/${skillId}`);
}
