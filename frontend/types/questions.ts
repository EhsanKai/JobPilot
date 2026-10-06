/** Application questions and answers (V0.9). Mirrors backend/app/schemas/application_question.py. */

export type QuestionType =
  | "FREE_TEXT"
  | "YES_NO"
  | "SINGLE_CHOICE"
  | "MULTI_CHOICE"
  | "NUMERIC"
  | "DATE"
  | "SALARY"
  | "UNKNOWN";

export const QUESTION_TYPES: QuestionType[] = [
  "FREE_TEXT",
  "YES_NO",
  "SINGLE_CHOICE",
  "MULTI_CHOICE",
  "NUMERIC",
  "DATE",
  "SALARY",
];

/**
 * NOT_STARTED   nothing yet
 * NEEDS_INPUT   JobPilot's trusted facts do not support an answer: the user answers
 * SUGGESTED     a validated suggestion exists; the user has not decided
 * ACCEPTED      the user accepted the suggestion unchanged
 * USER_EDITED   the user wrote or edited the answer
 * REJECTED      the user turned the suggestion down; no answer yet
 */
export type AnswerStatus =
  | "NOT_STARTED"
  | "NEEDS_INPUT"
  | "SUGGESTED"
  | "ACCEPTED"
  | "USER_EDITED"
  | "REJECTED";

/** Where the *final* answer came from. */
export type AnswerSource = "DETERMINISTIC" | "AI" | "USER" | "HYBRID";

/** How a suggestion was produced: from the application's own data, or by the AI. */
export type GenerationMethod = "DETERMINISTIC" | "AI";

export interface SourceFact {
  text: string;
  ref: string | null;
}

export interface AnswerProvenance {
  generation_id: string;
  method: GenerationMethod;
  prompt_version: string | null;
  prompt_hash: string | null;
  ai_provider: string | null;
  ai_model: string | null;
  resume_id: string | null;
  resume_version_number: number | null;
  resume_profile_id: string | null;
  application_context_snapshot_id: string | null;
  input_hash: string;
  question_fingerprint: string;
  confidence: string | null;
  max_length: number | null;
  generated_at: string;
  validation_status: "passed" | "failed" | "not_run" | "not_applicable";
}

export interface Answer {
  id: string;
  status: AnswerStatus;
  answer_source: AnswerSource | null;
  // The suggestion (never edited by the user)
  ai_suggestion: string | null;
  ai_selected_options: string[] | null;
  ai_source_fact_ids: string[];
  ai_provenance: AnswerProvenance | null;
  ai_generation_id: string | null;
  suggestion_method: GenerationMethod | null;
  suggestion_dismissed: boolean;
  suggestion_outdated: boolean;
  source_facts: Record<string, SourceFact>;
  needs_input_reason: string | null;
  needs_input_message: string | null;
  // The user's answer (never edited by the AI)
  final_answer: string | null;
  final_selected_options: string[] | null;
  basis_generation_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Question {
  id: string;
  application_id: string;
  question_text: string;
  normalized_question_text: string;
  question_hash: string;
  question_type: QuestionType;
  required: boolean;
  max_length: number | null;
  options: string[] | null;
  external_key: string | null;
  field_name: string | null;
  source: string | null;
  source_metadata: Record<string, unknown> | null;
  sort_order: number;
  answer: Answer;
  created_at: string;
  updated_at: string;
}

export interface QuestionBlocker {
  code: string;
  message: string;
}

export interface QuestionList {
  items: Question[];
  /** Whether an AI suggestion can be generated now. Answering manually is never blocked. */
  ai: { can_generate: boolean; blockers: QuestionBlocker[] };
}

export interface QuestionCreateRequest {
  question_text: string;
  question_type: QuestionType;
  required: boolean;
  max_length?: number | null;
  options?: string[] | null;
}

export interface QuestionUpdateRequest {
  question_text?: string;
  question_type?: QuestionType;
  required?: boolean;
  max_length?: number | null;
  options?: string[] | null;
}

export interface ManualAnswerRequest {
  answer?: string;
  selected_options?: string[];
  based_on_suggestion?: boolean;
}
