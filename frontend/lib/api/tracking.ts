import { api } from "@/lib/http";
import type {
  ApplicationListQuery,
  ApplicationList,
  StatusChangeRequest,
  StatusChangeResponse,
  StatusCounts,
  Timeline,
  TrackingUpdateRequest,
} from "@/types/application";

/** The query string of `GET /applications` for the given filters (empty ones are left out). */
export function applicationListPath(query: ApplicationListQuery = {}): string {
  const params = new URLSearchParams();
  params.set("page", String(query.page ?? 1));
  params.set("page_size", String(query.pageSize ?? 20));
  for (const status of query.statuses ?? []) params.append("status", status);
  if (query.company?.trim()) params.set("company", query.company.trim());
  if (query.source?.trim()) params.set("source", query.source.trim());
  if (query.appliedFrom) params.set("applied_from", query.appliedFrom);
  if (query.appliedTo) params.set("applied_to", query.appliedTo);
  if (query.sort && query.sort !== "updated") params.set("sort", query.sort);
  return `/applications?${params.toString()}`;
}

/** "My Applications": your applications, filtered and sorted by the server. */
export function listMyApplications(query: ApplicationListQuery = {}): Promise<ApplicationList> {
  return api.get<ApplicationList>(applicationListPath(query));
}

/** How many of your applications are in each status. */
export function getStatusCounts(): Promise<StatusCounts> {
  return api.get<StatusCounts>("/applications/summary");
}

/** The application's history, newest first: status changes, notes and submission attempts. */
export function getTimeline(applicationId: string): Promise<Timeline> {
  return api.get<Timeline>(`/applications/${encodeURIComponent(applicationId)}/timeline`);
}

/**
 * Change the tracking status yourself. Only the status, a note, a follow-up date and the
 * explicit `correction` flag are sent: the server decides the previous status, the time and
 * who acted. Nothing is sent to anyone else.
 */
export function changeStatus(
  applicationId: string,
  body: StatusChangeRequest
): Promise<StatusChangeResponse> {
  return api.patch<StatusChangeResponse>(
    `/applications/${encodeURIComponent(applicationId)}/status`,
    body
  );
}

/** Set (or clear) the follow-up date and/or add a note. */
export function updateTracking(
  applicationId: string,
  body: TrackingUpdateRequest
): Promise<StatusChangeResponse> {
  return api.patch<StatusChangeResponse>(
    `/applications/${encodeURIComponent(applicationId)}/tracking`,
    body
  );
}
