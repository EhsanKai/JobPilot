import type {
  FinalReviewRecord,
  FinalReviewResponse,
  ReviewState,
  SubmissionAttempt,
  SubmissionAuthorization,
} from "@/types/submission";

export const HASH = "a".repeat(64);

export function makeState(overrides: Partial<ReviewState> = {}): ReviewState {
  return {
    schema_version: 1,
    state_hash: HASH,
    observed: true,
    destination: {
      host: "jobs.example.com",
      domain: "example.com",
      url: "https://jobs.example.com/apply",
      company: "Example GmbH",
      position: "Senior Software Developer",
      source: "greenhouse",
    },
    fields: [
      {
        field_id: "f-email",
        field_key: "field:id:email",
        label: "Email address",
        input_type: "email",
        required: true,
        group: "personal",
        mapping: "AUTO_MAPPED",
        question_id: "q1",
        question: "Email address",
        value: "erika@example.com",
        value_label: "erika@example.com",
        source: "USER",
        approval: "APPROVED",
        verification: "VERIFIED",
      },
      {
        field_id: "f-salary",
        field_key: "field:id:salary",
        label: "Salary expectation",
        input_type: "number",
        required: false,
        group: "questions",
        mapping: "AUTO_MAPPED",
        question_id: "q2",
        question: "Salary expectation",
        value: "65000",
        value_label: "65000",
        source: "DETERMINISTIC",
        approval: "APPROVED",
        verification: "VERIFIED",
      },
      {
        field_id: "f-why",
        field_key: "field:id:why",
        label: "Why are you interested in this position?",
        input_type: "textarea",
        required: true,
        group: "questions",
        mapping: "AUTO_MAPPED",
        question_id: "q3",
        question: "Why are you interested in this position?",
        value: "Because of the team.",
        value_label: "Because of the team.",
        source: "ACCEPTED_AI",
        approval: "APPROVED",
        verification: "VERIFIED",
      },
    ],
    documents: [
      {
        field_id: "f-cv",
        field_key: "field:id:cv",
        label: "Resume / CV",
        required: true,
        file: "Erika - Tailored CV.docx:4096",
        document_type: "tailored_cv",
        document_id: "doc-4",
        document_label: "Tailored CV v4",
        version_number: 4,
        origin: "user_edited",
        content_hash: "c".repeat(64),
        source: "DOCUMENT",
        verification: "VERIFIED",
      },
    ],
    consents: [
      {
        field_id: "f-privacy",
        field_key: "field:id:privacy",
        input_type: "checkbox",
        text: "I agree to the processing of my personal data.",
        text_hash: "b".repeat(64),
        required: true,
        given: true,
        authorized: true,
        authorized_at: "2026-10-07T12:00:00Z",
      },
      {
        field_id: "f-pool",
        field_key: "field:id:pool",
        input_type: "checkbox",
        text: "Keep my profile in the talent pool.",
        text_hash: "d".repeat(64),
        required: false,
        given: false,
        authorized: false,
        authorized_at: null,
      },
    ],
    submit_control: { label: "Submit application" },
    blockers: [],
    warnings: [],
    ready: true,
    ...overrides,
  };
}

export function makeReviewRecord(overrides: Partial<FinalReviewRecord> = {}): FinalReviewRecord {
  return {
    id: "review-1",
    session_id: "session-1",
    status: "ACTIVE",
    state_hash: HASH,
    destination_host: "jobs.example.com",
    destination_domain: "example.com",
    destination_url: "https://jobs.example.com/apply",
    reviewed_at: "2026-10-07T12:30:00Z",
    invalidated_at: null,
    invalidation_reason: null,
    ...overrides,
  };
}

export function makeReview(overrides: Partial<FinalReviewResponse> = {}): FinalReviewResponse {
  return {
    current: makeState(),
    review: null,
    review_is_current: false,
    authorization: null,
    can_confirm_review: true,
    can_authorize: false,
    ...overrides,
  };
}

export function confirmedReview(): FinalReviewResponse {
  return makeReview({ review: makeReviewRecord(), review_is_current: true, can_authorize: true });
}

export function makeAuthorization(
  overrides: Partial<SubmissionAuthorization> = {}
): SubmissionAuthorization {
  return {
    id: "auth-1",
    session_id: "session-1",
    final_review_id: "review-1",
    reviewed_state_hash: HASH,
    destination_host: "jobs.example.com",
    status: "AUTHORIZED",
    authorized_at: "2026-10-07T12:31:00Z",
    expires_at: "2026-10-07T12:41:00Z",
    consumed_at: null,
    invalidated_at: null,
    invalidation_reason: null,
    ...overrides,
  };
}

export function makeAttempt(overrides: Partial<SubmissionAttempt> = {}): SubmissionAttempt {
  return {
    id: "attempt-1",
    session_id: "session-1",
    authorization_id: "auth-1",
    final_review_id: "review-1",
    reviewed_state_hash: HASH,
    destination_domain: "example.com",
    destination_url: "https://jobs.example.com/apply",
    started_at: "2026-10-07T12:32:00Z",
    submit_triggered_at: "2026-10-07T12:32:01Z",
    finished_at: "2026-10-07T12:32:03Z",
    result: "SUCCESS",
    message: "Application submitted successfully.",
    confirmation_reference: "ABC-12345",
    confirmation_summary: "thank you for your application",
    failure_code: null,
    failure_reason: null,
    user_verified_outcome: null,
    user_verified_at: null,
    company: "Example GmbH",
    position: "Senior Software Developer",
    ...overrides,
  };
}
