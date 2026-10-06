"use client";

import { useEffect, useRef, useState } from "react";

import { AnswerEditor } from "@/components/applications/AnswerEditor";
import { QuestionForm } from "@/components/applications/QuestionForm";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import {
  acceptSuggestion,
  clearAnswer,
  deleteQuestion,
  generateAnswer,
  regenerateAnswer,
  rejectSuggestion,
  saveAnswer,
  updateQuestion,
} from "@/lib/api/questions";
import {
  STATUS_LABELS,
  TYPE_LABELS,
  copyText,
  describeViolation,
  finalText,
  hasFinal,
  hasSuggestion,
  problemFromError,
  suggestionLabel,
  suggestionText,
  type QuestionProblem,
} from "@/lib/questions";
import type { ManualAnswerRequest, Question, QuestionCreateRequest } from "@/types/questions";

type Busy = "generate" | "accept" | "reject" | "save" | "clear" | "delete" | "question" | null;

type Confirm =
  | { kind: "generate" }
  | { kind: "replace" }
  | { kind: "clear" }
  | { kind: "delete" }
  | null;

interface Props {
  applicationId: string;
  question: Question;
  onChange: (question: Question) => void;
  onDeleted: (questionId: string) => void;
}

/**
 * One question of the application, with what JobPilot suggests kept visibly apart
 * from what the user has decided:
 *
 *     AI suggestion   Accept / Edit / Regenerate / Reject     (never edited by the user)
 *     Your answer     Edit / Copy / Clear                      (never edited by the AI)
 *
 * The user can always answer by hand; a new suggestion never changes their answer.
 */
export function QuestionCard({ applicationId, question, onChange, onDeleted }: Props) {
  const { answer } = question;
  const [busy, setBusy] = useState<Busy>(null);
  const [problem, setProblem] = useState<QuestionProblem | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [editing, setEditing] = useState<{ fromSuggestion: boolean } | null>(null);
  const [editingQuestion, setEditingQuestion] = useState(false);
  const [copied, setCopied] = useState<boolean | null>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const final = hasFinal(answer);
  const suggestion = hasSuggestion(answer);
  const generated = answer.ai_generation_id !== null;

  async function run(kind: Exclude<Busy, null>, action: () => Promise<Question | void>) {
    setBusy(kind);
    setProblem(null);
    setConfirm(null);
    try {
      const result = await action();
      if (!isMounted.current) return;
      if (result) onChange(result);
      setEditing(null);
      setEditingQuestion(false);
    } catch (error) {
      if (isMounted.current) setProblem(problemFromError(error));
    } finally {
      if (isMounted.current) setBusy(null);
    }
  }

  function generate(acknowledged = false) {
    if (final && !acknowledged) {
      setConfirm({ kind: "generate" });
      return;
    }
    void run("generate", () =>
      generated
        ? regenerateAnswer(applicationId, question.id, final)
        : generateAnswer(applicationId, question.id, final)
    );
  }

  function accept(replace = false) {
    if (final && !replace) {
      setConfirm({ kind: "replace" });
      return;
    }
    void run("accept", () => acceptSuggestion(applicationId, question.id, replace));
  }

  function save(body: ManualAnswerRequest) {
    void run("save", () => saveAnswer(applicationId, question.id, body));
  }

  async function copy() {
    const ok = await copyText(finalText(answer));
    if (!isMounted.current) return;
    setCopied(ok);
    setTimeout(() => {
      if (isMounted.current) setCopied(null);
    }, 2500);
  }

  function saveQuestion(body: QuestionCreateRequest) {
    void run("question", () =>
      updateQuestion(applicationId, question.id, {
        question_text: body.question_text,
        question_type: body.question_type,
        required: body.required,
        max_length: body.max_length ?? null,
        options: body.options ?? null,
      })
    );
  }

  const confirmCopy: Record<NonNullable<Confirm>["kind"], string> = {
    generate:
      answer.answer_source === "USER" || answer.answer_source === "HYBRID"
        ? "Your current answer has been manually edited. Generate a new AI suggestion without changing your current answer?"
        : "You have already accepted an answer. Generate a new suggestion without changing your current answer?",
    replace:
      "You already have an answer. Replace it with this suggestion? Your current answer will be replaced.",
    clear: "Remove your answer? The suggestion and its history stay.",
    delete: "Delete this question and its answer? This cannot be undone.",
  };

  const headingId = `question-${question.id}-heading`;
  const statusChip =
    !final && answer.status === "SUGGESTED" ? suggestionLabel(answer) : STATUS_LABELS[answer.status];

  return (
    <article
      aria-labelledby={headingId}
      data-question-id={question.id}
      data-status={answer.status}
      className="rounded-lg border border-slate-200 p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={headingId} className="text-base font-semibold text-slate-900">
            {question.question_text}
          </h3>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>{TYPE_LABELS[question.question_type]}</span>
            {question.required && <span className="font-medium text-slate-700">Required</span>}
            {question.max_length !== null && <span>Max {question.max_length} characters</span>}
            <span
              data-testid="answer-status"
              className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-700"
            >
              {statusChip}
            </span>
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            disabled={busy !== null}
            onClick={() => setEditingQuestion((value) => !value)}
          >
            Edit question
          </Button>
          <Button
            variant="secondary"
            disabled={busy !== null}
            onClick={() => setConfirm({ kind: "delete" })}
          >
            Delete question
          </Button>
        </div>
      </div>

      {editingQuestion && (
        <div className="mt-3 rounded-lg bg-slate-50 p-3">
          <QuestionForm
            question={question}
            idPrefix={`edit-${question.id}`}
            isSaving={busy === "question"}
            onSubmit={saveQuestion}
            onCancel={() => setEditingQuestion(false)}
          />
        </div>
      )}

      {confirm && (
        <div role="group" aria-label="Confirm" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p>{confirmCopy[confirm.kind]}</p>
          <div className="mt-2 flex gap-2">
            <Button
              variant={confirm.kind === "delete" ? "danger" : "primary"}
              onClick={() => {
                if (confirm.kind === "generate") generate(true);
                else if (confirm.kind === "replace") accept(true);
                else if (confirm.kind === "clear")
                  void run("clear", () => clearAnswer(applicationId, question.id));
                else void run("delete", async () => {
                  await deleteQuestion(applicationId, question.id);
                  onDeleted(question.id);
                });
              }}
            >
              Continue
            </Button>
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {problem && (
        <div className="mt-3">
          {problem.kind === "blocked" ? (
            <Alert variant="info">{problem.message}</Alert>
          ) : (
            <Alert variant="error">
              <p>{problem.message}</p>
              {problem.violations.length > 0 && (
                <ul className="mt-2 list-disc pl-5" aria-label="Why it was rejected">
                  {problem.violations.map((violation, index) => (
                    <li key={index}>{describeViolation(violation)}</li>
                  ))}
                </ul>
              )}
            </Alert>
          )}
        </div>
      )}

      {answer.status === "NEEDS_INPUT" && (
        <div className="mt-3" data-testid="needs-input">
          <Alert variant="info">
            <p className="font-medium">Needs your input</p>
            <p>{answer.needs_input_message}</p>
          </Alert>
        </div>
      )}

      {suggestion && (
        <section
          aria-label={suggestionLabel(answer)}
          className="mt-3 rounded-lg border border-brand-100 bg-brand-50/40 p-3"
        >
          <h4 className="text-sm font-semibold text-slate-700">
            {suggestionLabel(answer)}
            {answer.suggestion_dismissed && (
              <span className="ml-2 font-normal text-slate-500">· rejected by you</span>
            )}
          </h4>
          <p
            data-testid="suggestion-text"
            className={`mt-1 whitespace-pre-wrap text-sm ${
              answer.suggestion_dismissed ? "text-slate-400 line-through" : "text-slate-900"
            }`}
          >
            {suggestionText(answer)}
          </p>
          {answer.suggestion_outdated && (
            <p className="mt-1 text-xs font-medium text-amber-700">
              The question changed since this was suggested. Regenerate to get a fresh suggestion.
            </p>
          )}
          <p className="mt-1 text-xs text-slate-500">
            {answer.suggestion_method === "DETERMINISTIC"
              ? "Built from your application data. No AI was used."
              : `Checked against your resume before it was shown${
                  answer.ai_provenance?.ai_provider
                    ? ` · ${answer.ai_provenance.ai_provider}${
                        answer.ai_provenance.ai_model ? ` (${answer.ai_provenance.ai_model})` : ""
                      } · ${answer.ai_provenance.prompt_version}`
                    : ""
                }`}
          </p>
          {Object.keys(answer.source_facts).length > 0 && (
            <details className="mt-1 text-xs text-slate-500">
              <summary className="cursor-pointer select-none hover:text-slate-700">
                Sources ({Object.keys(answer.source_facts).length})
              </summary>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {Object.entries(answer.source_facts).map(([id, fact]) => (
                  <li key={id}>{fact.text}</li>
                ))}
              </ul>
            </details>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            {answer.status !== "ACCEPTED" && (
              <Button isLoading={busy === "accept"} disabled={busy !== null} onClick={() => accept()}>
                Accept
              </Button>
            )}
            {!final && (
              <Button
                variant="secondary"
                disabled={busy !== null}
                onClick={() => setEditing({ fromSuggestion: true })}
              >
                Edit
              </Button>
            )}
            <Button
              variant="secondary"
              isLoading={busy === "generate"}
              disabled={busy !== null}
              onClick={() => generate()}
            >
              Regenerate
            </Button>
            {!answer.suggestion_dismissed && (
              <Button
                variant="secondary"
                isLoading={busy === "reject"}
                disabled={busy !== null}
                onClick={() => void run("reject", () => rejectSuggestion(applicationId, question.id))}
              >
                Reject
              </Button>
            )}
          </div>
        </section>
      )}

      <section aria-label="Your answer" className="mt-3 rounded-lg border border-slate-200 p-3">
        <h4 className="text-sm font-semibold text-slate-700">
          Your answer
          {final && (
            <span className="ml-2 font-normal text-slate-500">
              · {answer.status === "ACCEPTED" ? "accepted suggestion" : "edited by you"}
            </span>
          )}
        </h4>
        {editing ? (
          <AnswerEditor
            question={question}
            initialText={editing.fromSuggestion ? (answer.ai_suggestion ?? "") : (answer.final_answer ?? "")}
            initialOptions={
              (editing.fromSuggestion ? answer.ai_selected_options : answer.final_selected_options) ?? []
            }
            basedOnSuggestion={editing.fromSuggestion}
            isSaving={busy === "save"}
            onSave={save}
            onCancel={() => setEditing(null)}
          />
        ) : final ? (
          <>
            <p data-testid="final-answer" className="mt-1 whitespace-pre-wrap text-sm text-slate-900">
              {finalText(answer)}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                disabled={busy !== null}
                onClick={() => setEditing({ fromSuggestion: false })}
              >
                Edit
              </Button>
              <Button variant="secondary" onClick={() => void copy()}>
                Copy
              </Button>
              <Button
                variant="secondary"
                disabled={busy !== null}
                onClick={() => setConfirm({ kind: "clear" })}
              >
                Clear
              </Button>
              {copied !== null && (
                <span role="status" className="text-xs text-slate-500">
                  {copied ? "Copied" : "Copy failed: select the text and copy it yourself."}
                </span>
              )}
            </div>
          </>
        ) : (
          <>
            <p className="mt-1 text-sm text-slate-500">Not answered yet.</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button
                variant="secondary"
                disabled={busy !== null}
                onClick={() => setEditing({ fromSuggestion: false })}
              >
                Answer manually
              </Button>
              {!suggestion && (
                <Button
                  isLoading={busy === "generate"}
                  disabled={busy !== null}
                  onClick={() => generate()}
                >
                  {generated ? "Try again" : "Generate answer"}
                </Button>
              )}
            </div>
          </>
        )}
        {final && !suggestion && !editing && (
          <div className="mt-2">
            <Button
              variant="secondary"
              isLoading={busy === "generate"}
              disabled={busy !== null}
              onClick={() => generate()}
            >
              {generated ? "Try again" : "Generate answer"}
            </Button>
            <span className="ml-2 text-xs text-slate-500">
              A suggestion never changes your answer.
            </span>
          </div>
        )}
      </section>
    </article>
  );
}
