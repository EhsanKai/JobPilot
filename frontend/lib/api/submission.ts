import { api } from "@/lib/http";
import type {
  FinalReviewRecord,
  FinalReviewResponse,
  SubmissionAttempt,
  SubmissionAuthorization,
  SubmissionList,
} from "@/types/submission";

/**
 * Review & submit (V0.11). Nothing here sends a URL, a selector or form values: the
 * server derives what is submitted, and where, from the review you confirmed.
 */

function automation(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/automation`;
}

function session(applicationId: string, sessionId: string): string {
  return `${automation(applicationId)}/${encodeURIComponent(sessionId)}`;
}

/** Exactly what would be submitted now, read from the live page. Reads only. */
export function getFinalReview(applicationId: string, sessionId: string): Promise<FinalReviewResponse> {
  return api.get<FinalReviewResponse>(`${session(applicationId, sessionId)}/final-review`);
}

/** Confirm the final review you looked at (by its hash). */
export function confirmFinalReview(
  applicationId: string,
  sessionId: string,
  stateHash: string
): Promise<FinalReviewRecord> {
  return api.post<FinalReviewRecord>(`${session(applicationId, sessionId)}/final-review`, {
    expected_state_hash: stateHash,
  });
}

/** Give one consent, for exactly the text you were shown. With `give`, JobPilot ticks it. */
export function authorizeConsent(
  applicationId: string,
  sessionId: string,
  fieldId: string,
  textHash: string,
  give = true
): Promise<void> {
  return api.post<void>(
    `${session(applicationId, sessionId)}/consents/${encodeURIComponent(fieldId)}`,
    { consent_text_hash: textHash, give }
  );
}

export function revokeConsent(applicationId: string, sessionId: string, fieldId: string): Promise<void> {
  return api.delete<void>(
    `${session(applicationId, sessionId)}/consents/${encodeURIComponent(fieldId)}`
  );
}

/** Your explicit authorization to submit the reviewed application. */
export function authorizeSubmission(
  applicationId: string,
  sessionId: string,
  finalReviewId: string,
  reviewedStateHash: string
): Promise<SubmissionAuthorization> {
  return api.post<SubmissionAuthorization>(
    `${session(applicationId, sessionId)}/submission-authorization`,
    { final_review_id: finalReviewId, reviewed_state_hash: reviewedStateHash, confirm: true }
  );
}

export function cancelAuthorization(applicationId: string, sessionId: string): Promise<void> {
  return api.delete<void>(`${session(applicationId, sessionId)}/submission-authorization`);
}

/** Submit under an authorization -- once. Takes the authorization id and nothing else. */
export function submitApplication(
  applicationId: string,
  sessionId: string,
  authorizationId: string
): Promise<SubmissionAttempt> {
  return api.post<SubmissionAttempt>(`${session(applicationId, sessionId)}/submit`, {
    authorization_id: authorizationId,
  });
}

export function listSubmissions(applicationId: string): Promise<SubmissionList> {
  return api.get<SubmissionList>(`${automation(applicationId)}/submissions`);
}

/** After an unknown result: what you found when you checked yourself. */
export function verifySubmission(
  applicationId: string,
  attemptId: string,
  outcome: "SUBMITTED" | "NOT_SUBMITTED"
): Promise<SubmissionAttempt> {
  return api.post<SubmissionAttempt>(
    `${automation(applicationId)}/submissions/${encodeURIComponent(attemptId)}/verification`,
    { outcome }
  );
}
