import { api } from "@/lib/http";
import type {
  DesiredJobTitle,
  DesiredJobTitleInput,
  JobPreferences,
  JobPreferencesInput,
  PreferredLocation,
  PreferredLocationInput,
} from "@/types/preferences";

export function getPreferences(): Promise<JobPreferences> {
  return api.get<JobPreferences>("/preferences");
}

export function updatePreferences(input: JobPreferencesInput): Promise<JobPreferences> {
  return api.put<JobPreferences>("/preferences", input);
}

export function addJobTitle(input: DesiredJobTitleInput): Promise<DesiredJobTitle> {
  return api.post<DesiredJobTitle>("/preferences/job-titles", input);
}

export function deleteJobTitle(jobTitleId: string): Promise<void> {
  return api.delete<void>(`/preferences/job-titles/${jobTitleId}`);
}

export function addLocation(input: PreferredLocationInput): Promise<PreferredLocation> {
  return api.post<PreferredLocation>("/preferences/locations", input);
}

export function deleteLocation(locationId: string): Promise<void> {
  return api.delete<void>(`/preferences/locations/${locationId}`);
}
