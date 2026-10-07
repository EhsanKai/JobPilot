import { api } from "@/lib/http";
import type {
  ManualAnswerRequest,
  Question,
  QuestionCreateRequest,
  QuestionList,
  QuestionUpdateRequest,
} from "@/types/questions";

function base(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/questions`;
}

function one(applicationId: string, questionId: string): string {
  return `${base(applicationId)}/${encodeURIComponent(questionId)}`;
}

export function listQuestions(applicationId: string): Promise<QuestionList> {
  return api.get<QuestionList>(base(applicationId));
}

export function createQuestion(
  applicationId: string,
  body: QuestionCreateRequest
): Promise<Question> {
  return api.post<Question>(base(applicationId), body);
}

export function updateQuestion(
  applicationId: string,
  questionId: string,
  body: QuestionUpdateRequest
): Promise<Question> {
  return api.patch<Question>(one(applicationId, questionId), body);
}

export function deleteQuestion(applicationId: string, questionId: string): Promise<void> {
  return api.delete<void>(one(applicationId, questionId));
}

/**
 * Suggest a first answer: from the application's own data when it states it (no
 * AI), else with the AI. Only the *suggestion* is ever produced; the user's answer
 * is never changed.
 */
export function generateAnswer(
  applicationId: string,
  questionId: string,
  acknowledgeExistingAnswer = false
): Promise<Question> {
  return api.post<Question>(
    `${one(applicationId, questionId)}/generate`,
    acknowledgeExistingAnswer ? { acknowledge_existing_answer: true } : undefined
  );
}

/** A new suggestion. `acknowledgeExistingAnswer` confirms the user knows they have an answer. */
export function regenerateAnswer(
  applicationId: string,
  questionId: string,
  acknowledgeExistingAnswer = false
): Promise<Question> {
  return api.post<Question>(
    `${one(applicationId, questionId)}/regenerate`,
    acknowledgeExistingAnswer ? { acknowledge_existing_answer: true } : undefined
  );
}

/** Make the current suggestion the answer, unchanged (the server copies it). */
export function acceptSuggestion(
  applicationId: string,
  questionId: string,
  replaceExisting = false
): Promise<Question> {
  return api.post<Question>(
    `${one(applicationId, questionId)}/accept`,
    replaceExisting ? { replace_existing: true } : undefined
  );
}

export function rejectSuggestion(applicationId: string, questionId: string): Promise<Question> {
  return api.post<Question>(`${one(applicationId, questionId)}/reject`);
}

/** The user's own answer: needs no AI at all. */
export function saveAnswer(
  applicationId: string,
  questionId: string,
  body: ManualAnswerRequest
): Promise<Question> {
  return api.post<Question>(`${one(applicationId, questionId)}/manual`, body);
}

export function clearAnswer(applicationId: string, questionId: string): Promise<Question> {
  return api.post<Question>(`${one(applicationId, questionId)}/clear`);
}
