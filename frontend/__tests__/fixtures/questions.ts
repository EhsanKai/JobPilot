import type { Answer, Question, QuestionList } from "@/types/questions";

let counter = 0;

export function makeAnswer(overrides: Partial<Answer> = {}): Answer {
  counter += 1;
  return {
    id: `answer-${counter}`,
    status: "NOT_STARTED",
    answer_source: null,
    ai_suggestion: null,
    ai_selected_options: null,
    ai_source_fact_ids: [],
    ai_provenance: null,
    ai_generation_id: null,
    suggestion_method: null,
    suggestion_dismissed: false,
    suggestion_outdated: false,
    source_facts: {},
    needs_input_reason: null,
    needs_input_message: null,
    final_answer: null,
    final_selected_options: null,
    basis_generation_id: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

/** An AI suggestion that is waiting for the user's decision. */
export function suggestedAnswer(text = "I develop SAP IS-U applications.", overrides: Partial<Answer> = {}) {
  return makeAnswer({
    status: "SUGGESTED",
    ai_suggestion: text,
    ai_source_fact_ids: ["exp1.r1"],
    ai_generation_id: "gen-1",
    suggestion_method: "AI",
    source_facts: { "exp1.r1": { text: "Developed and maintained SAP IS-U applications", ref: null } },
    ai_provenance: {
      generation_id: "gen-1",
      method: "AI",
      prompt_version: "application_answer_v1",
      prompt_hash: "d".repeat(64),
      ai_provider: "anthropic",
      ai_model: "claude-sonnet-5-5",
      resume_id: "resume-1",
      resume_version_number: 1,
      resume_profile_id: "profile-1",
      application_context_snapshot_id: "snapshot-1",
      input_hash: "e".repeat(64),
      question_fingerprint: "f".repeat(64),
      confidence: "HIGH",
      max_length: null,
      generated_at: new Date().toISOString(),
      validation_status: "passed",
    },
    ...overrides,
  });
}

export function makeQuestion(overrides: Partial<Question> = {}): Question {
  counter += 1;
  const text = overrides.question_text ?? "Why do you want to work for our company?";
  return {
    id: `q-${counter}`,
    application_id: "app-1",
    question_text: text,
    normalized_question_text: text.toLowerCase(),
    question_hash: "a".repeat(64),
    question_type: "FREE_TEXT",
    required: false,
    max_length: null,
    options: null,
    external_key: null,
    field_name: null,
    source: "manual",
    source_metadata: null,
    sort_order: counter,
    answer: makeAnswer(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function makeQuestionList(
  items: Question[] = [],
  ai: QuestionList["ai"] = { can_generate: true, blockers: [] }
): QuestionList {
  return { items, ai };
}
