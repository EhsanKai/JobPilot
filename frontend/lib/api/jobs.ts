import { api } from "@/lib/http";
import type {
  DiscoveryRun,
  JobDetail,
  JobList,
  JobListQuery,
  JobSearchCriteria,
  JobSource,
} from "@/types/jobs";

function toQueryString(query: JobListQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function listJobs(query: JobListQuery = {}): Promise<JobList> {
  return api.get<JobList>(`/jobs${toQueryString(query)}`);
}

export function getJob(jobId: string): Promise<JobDetail> {
  return api.get<JobDetail>(`/jobs/${encodeURIComponent(jobId)}`);
}

export function saveJob(jobId: string): Promise<JobDetail> {
  return api.put<JobDetail>(`/jobs/${encodeURIComponent(jobId)}/save`);
}

export function unsaveJob(jobId: string): Promise<JobDetail> {
  return api.delete<JobDetail>(`/jobs/${encodeURIComponent(jobId)}/save`);
}

export function listJobSources(): Promise<JobSource[]> {
  return api.get<JobSource[]>("/jobs/sources");
}

export function getDefaultCriteria(): Promise<JobSearchCriteria> {
  return api.get<JobSearchCriteria>("/jobs/criteria/defaults");
}

/** Start discovery. Without criteria, the backend uses the user's job preferences. */
export function startDiscovery(criteria?: JobSearchCriteria): Promise<DiscoveryRun> {
  return api.post<DiscoveryRun>("/jobs/discover", criteria ? { criteria } : {});
}

export function getDiscoveryRun(runId: string): Promise<DiscoveryRun> {
  return api.get<DiscoveryRun>(`/jobs/discover/runs/${encodeURIComponent(runId)}`);
}

export function getLatestDiscoveryRun(): Promise<DiscoveryRun | null> {
  return api.get<DiscoveryRun | null>("/jobs/discover/runs/latest");
}
