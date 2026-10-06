/** Browser assistant (V0.10). Mirrors backend/app/schemas/automation.py. */

import type { AnswerStatus, QuestionType } from "@/types/questions";

/** There is deliberately no SUBMITTED: V0.10 never submits an application. */
export type AutomationStatus =
  | "STARTING"
  | "RUNNING"
  | "PAUSED"
  | "USER_CONTROL"
  | "BLOCKED"
  | "COMPLETED"
  | "STOPPED"
  | "FAILED";

export const ACTIVE_STATUSES: AutomationStatus[] = [
  "STARTING",
  "RUNNING",
  "PAUSED",
  "USER_CONTROL",
  "BLOCKED",
  "COMPLETED",
];

export type MappingStatus = "UNMAPPED" | "AUTO_MAPPED" | "USER_CONFIRMED" | "NEEDS_REVIEW";
export type FillStatus =
  | "NOT_FILLED"
  | "FILLED"
  | "FILL_FAILED"
  | "USER_MODIFIED"
  | "USER_ACTION_REQUIRED";

export type DisplayStatus =
  | "filled"
  | "user_modified"
  | "needs_input"
  | "needs_review"
  | "action_required"
  | "ready"
  | "failed"
  | "unmapped";

export type AnswerState = "APPROVED" | "NEEDS_INPUT" | "SUGGESTION_AVAILABLE" | "UNFILLABLE";
export type AnswerSourceLabel = "USER" | "DETERMINISTIC" | "ACCEPTED_AI";
export type DocumentType = "tailored_cv" | "cover_letter" | "resume_file";

export interface Attention {
  code: string;
  message: string;
}

export interface SessionError {
  code: string | null;
  message: string;
}

export interface SessionCounts {
  found: number;
  ready: number;
  filled: number;
  needs_review: number;
  needs_input: number;
  user_modified: number;
  action_required: number;
  failed: number;
  unmapped: number;
}

/** What the user can do right now (the buttons). */
export interface SessionControls {
  can_pause: boolean;
  can_resume: boolean;
  can_take_control: boolean;
  can_stop: boolean;
  can_discover: boolean;
  can_fill: boolean;
  can_confirm_domain: boolean;
}

export interface AutomationSession {
  id: string;
  application_id: string;
  status: AutomationStatus;
  status_label: string;
  target_url: string;
  current_url: string | null;
  page_title: string | null;
  browser_mode: "visible" | "headless";
  browser_alive: boolean;
  attention: Attention | null;
  submit_detected: boolean;
  submit_notice: string | null;
  last_error: SessionError | null;
  counts: SessionCounts;
  controls: SessionControls;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionList {
  items: AutomationSession[];
  can_start: boolean;
  cannot_start_reason: string | null;
  cannot_start_message: string | null;
}

export interface QuestionBrief {
  id: string;
  question_text: string;
  question_type: QuestionType;
  required: boolean;
  source: string | null;
  answer_status: AnswerStatus;
}

export interface Mapping {
  status: MappingStatus;
  method: string | null;
  confidence: "HIGH" | "MEDIUM" | "LOW" | null;
  reason: string | null;
}

export interface FieldAnswer {
  state: AnswerState;
  source: AnswerSourceLabel | null;
  text: string | null;
  options: string[] | null;
  reason: string | null;
  message: string | null;
}

export interface FillInfo {
  status: FillStatus;
  error_code: string | null;
  error_message: string | null;
  last_filled_at: string | null;
}

export interface DocumentChoice {
  type: DocumentType;
  id: string;
  label: string;
  version_number: number | null;
  origin: string;
  is_current: boolean;
  file_name: string;
}

export interface FieldDocument {
  detected_type: "tailored_cv" | "cover_letter" | null;
  proposed: DocumentChoice | null;
  selected: DocumentChoice | null;
  confirmed: boolean;
  newer_available: boolean;
  format_accepted: boolean;
}

export interface FormField {
  id: string;
  field_key: string;
  position: number;
  input_type: string;
  label: string;
  name: string | null;
  dom_id: string | null;
  form_context: string | null;
  required: boolean;
  max_length: number | null;
  options: { value: string; label: string }[] | null;
  present: boolean;
  display_status: DisplayStatus;
  display_message: string | null;
  required_missing: boolean;
  mapping: Mapping;
  question: QuestionBrief | null;
  proposed_question: QuestionBrief | null;
  answer: FieldAnswer | null;
  fill: FillInfo;
  user_action_reason: string | null;
  user_action_message: string | null;
  document: FieldDocument | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface FieldList {
  items: FormField[];
  /** Existing questions a field can be (re)mapped to. */
  questions: QuestionBrief[];
}

export interface AutomationEvent {
  id: string;
  event_type: string;
  field_id: string | null;
  data: Record<string, string | number | boolean | null>;
  created_at: string;
}

export type MappingAction =
  | { action: "confirm" }
  | { action: "unmap" }
  | { action: "create" }
  | { action: "change"; question_id: string };

export interface FillOptions {
  field_ids?: string[];
  overwrite_field_ids?: string[];
}

export interface SuggestResult {
  requested: number;
  suggested: number;
  failed: number;
  session: AutomationSession;
}
