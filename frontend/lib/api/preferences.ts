import { api } from "@/lib/http";
import type { JobPreferences, JobPreferencesInput } from "@/types/preferences";

export function getPreferences(): Promise<JobPreferences> {
  return api.get<JobPreferences>("/preferences");
}

export function updatePreferences(input: JobPreferencesInput): Promise<JobPreferences> {
  return api.put<JobPreferences>("/preferences", input);
}
