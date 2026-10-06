import { api } from "@/lib/http";
import type { AIExplanation, BatchMatchResponse, MatchState } from "@/types/match";

export function getMatch(jobId: string): Promise<MatchState> {
  return api.get<MatchState>(`/jobs/${encodeURIComponent(jobId)}/match`);
}

/** Calculate or recalculate the match (deterministic, no AI). */
export function calculateMatch(jobId: string): Promise<MatchState> {
  return api.post<MatchState>(`/jobs/${encodeURIComponent(jobId)}/match`);
}

/** Deterministic matching for several jobs (at most 25 ids). */
export function calculateMatches(jobIds: string[]): Promise<BatchMatchResponse> {
  return api.post<BatchMatchResponse>("/jobs/match", { job_ids: jobIds });
}

/** AI-written explanation of the current match (validated and cached server-side). */
export function explainMatch(jobId: string): Promise<AIExplanation> {
  return api.post<AIExplanation>(`/jobs/${encodeURIComponent(jobId)}/match/explanation`);
}
