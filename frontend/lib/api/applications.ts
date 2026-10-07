import { api } from "@/lib/http";
import type {
  Application,
  ApplicationList,
  ApplicationStartRequest,
  ApplicationUpdateRequest,
} from "@/types/application";

/**
 * Start (create) your application for a job, or get the one you already have
 * (starting twice never creates a second). This only prepares a private
 * workspace: nothing is submitted or sent anywhere.
 */
export function startApplication(
  jobId: string,
  body: ApplicationStartRequest = {}
): Promise<Application> {
  return api.post<Application>(`/jobs/${encodeURIComponent(jobId)}/application`, body);
}

export function getApplication(applicationId: string): Promise<Application> {
  return api.get<Application>(`/applications/${encodeURIComponent(applicationId)}`);
}

/** Update only the changed application-specific fields. */
export function updateApplication(
  applicationId: string,
  patch: ApplicationUpdateRequest
): Promise<Application> {
  return api.patch<Application>(`/applications/${encodeURIComponent(applicationId)}`, patch);
}

/** Page of your applications (see `lib/api/tracking.ts` for the filtered "My Applications" list). */
export function listApplications(page = 1, pageSize = 20): Promise<ApplicationList> {
  return api.get<ApplicationList>(`/applications?page=${page}&page_size=${pageSize}`);
}
