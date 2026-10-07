/** Review & submit (V0.11). Mirrors backend/app/schemas/submission.py. */

export type FinalReviewStatus = "ACTIVE" | "INVALIDATED" | "SUPERSEDED" | "USED";
export type AuthorizationStatus = "AUTHORIZED" | "CONSUMED" | "EXPIRED" | "INVALIDATED";
/** A pressed button is not a success: SUCCESS needs evidence the page showed. */
export type SubmissionResult = "SUCCESS" | "FAILED_BEFORE_SUBMIT" | "SUBMISSION_UNKNOWN";
export type ValueSource =
  | "USER"
  | "DETERMINISTIC"
  | "ACCEPTED_AI"
  | "DOCUMENT"
  | "BROWSER_DISCOVERED";

export interface Issue {
  code: string;
  message: string;
  field_id: string | null;
  label: string | null;
}

export interface Destination {
  host: string;
  domain: string;
  url: string;
  company: string | null;
  position: string | null;
  source: string | null;
}

export interface ReviewField {
  field_id: string | null;
  field_key: string;
  label: string;
  input_type: string;
  required: boolean;
  group: "personal" | "questions" | string;
  mapping: string;
  question_id: string | null;
  question: string | null;
  value: string | null;
  value_label: string | null;
  source: ValueSource | null;
  approval: string;
  verification: string;
}

export interface ReviewDocument {
  field_id: string | null;
  field_key: string;
  label: string;
  required: boolean;
  file: string | null;
  document_type: string | null;
  document_id: string | null;
  document_label: string | null;
  version_number: number | null;
  origin: string | null;
  content_hash: string | null;
  source: ValueSource | null;
  verification: string;
}

export interface ReviewConsent {
  field_id: string | null;
  field_key: string;
  input_type: string;
  /** The exact consent text the page shows. */
  text: string;
  text_hash: string;
  required: boolean;
  given: boolean;
  authorized: boolean;
  authorized_at: string | null;
}

export interface ReviewState {
  schema_version: number;
  state_hash: string;
  observed: boolean;
  destination: Destination;
  fields: ReviewField[];
  documents: ReviewDocument[];
  consents: ReviewConsent[];
  submit_control: { label: string } | null;
  blockers: Issue[];
  warnings: Issue[];
  ready: boolean;
}

export interface FinalReviewRecord {
  id: string;
  session_id: string;
  status: FinalReviewStatus;
  state_hash: string;
  destination_host: string;
  destination_domain: string;
  destination_url: string;
  reviewed_at: string;
  invalidated_at: string | null;
  invalidation_reason: string | null;
}

export interface SubmissionAuthorization {
  id: string;
  session_id: string;
  final_review_id: string;
  reviewed_state_hash: string;
  destination_host: string;
  status: AuthorizationStatus;
  authorized_at: string;
  expires_at: string;
  consumed_at: string | null;
  invalidated_at: string | null;
  invalidation_reason: string | null;
}

export interface FinalReviewResponse {
  current: ReviewState;
  review: FinalReviewRecord | null;
  review_is_current: boolean;
  authorization: SubmissionAuthorization | null;
  can_confirm_review: boolean;
  can_authorize: boolean;
}

export interface SubmissionAttempt {
  id: string;
  session_id: string;
  authorization_id: string;
  final_review_id: string;
  reviewed_state_hash: string;
  destination_domain: string;
  destination_url: string;
  started_at: string;
  submit_triggered_at: string | null;
  finished_at: string | null;
  /** null while the attempt runs. */
  result: SubmissionResult | null;
  message: string;
  confirmation_reference: string | null;
  confirmation_summary: string | null;
  failure_code: string | null;
  failure_reason: string | null;
  user_verified_outcome: "SUBMITTED" | "NOT_SUBMITTED" | null;
  user_verified_at: string | null;
  company: string | null;
  position: string | null;
}

export interface SubmissionList {
  items: SubmissionAttempt[];
  submitted: boolean;
  needs_verification: boolean;
}
