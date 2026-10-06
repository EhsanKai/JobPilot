"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { QuestionCard } from "@/components/applications/QuestionCard";
import { QuestionForm } from "@/components/applications/QuestionForm";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { createQuestion, listQuestions } from "@/lib/api/questions";
import { ApiError } from "@/lib/http";
import type { Question, QuestionCreateRequest, QuestionList } from "@/types/questions";

interface Props {
  applicationId: string;
}

/**
 * The questions the employer asks, as part of the application. In V0.9 the user
 * adds them; each can be answered entirely by hand, and JobPilot can suggest an
 * answer grounded in the confirmed resume and the application's own data. Its
 * suggestions never overwrite the user's answer, and it never invents one.
 */
export function ApplicationQuestions({ applicationId }: Props) {
  const [list, setList] = useState<QuestionList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const loaded = await listQuestions(applicationId);
      if (!isMounted.current) return;
      setList(loaded);
      setLoadError(null);
    } catch {
      if (isMounted.current) setLoadError("Unable to load your questions. Please try again.");
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function create(body: QuestionCreateRequest) {
    setIsCreating(true);
    setCreateError(null);
    try {
      const created = await createQuestion(applicationId, body);
      if (!isMounted.current) return;
      setList((current) =>
        current ? { ...current, items: [...current.items, created] } : current
      );
      setAdding(false);
    } catch (error) {
      if (isMounted.current) {
        setCreateError(
          error instanceof ApiError ? error.message : "Unable to add the question. Please try again."
        );
      }
    } finally {
      if (isMounted.current) setIsCreating(false);
    }
  }

  function replace(updated: Question) {
    setList((current) =>
      current
        ? { ...current, items: current.items.map((q) => (q.id === updated.id ? updated : q)) }
        : current
    );
  }

  function remove(questionId: string) {
    setList((current) =>
      current ? { ...current, items: current.items.filter((q) => q.id !== questionId) } : current
    );
  }

  return (
    <Card aria-labelledby="questions-heading" id="questions">
      <h2 id="questions-heading" className="text-lg font-semibold text-slate-900">
        Application questions
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Add the questions the employer asks. You can answer every one yourself. JobPilot can
        suggest answers from your confirmed resume and this application&apos;s data, and says
        &ldquo;needs your input&rdquo; instead of guessing. A suggestion never replaces your
        answer, and you can copy your final answer wherever the question appears.
      </p>

      {loadError && (
        <div className="mt-3">
          <Alert variant="error">{loadError}</Alert>
        </div>
      )}

      {list !== null && !list.ai.can_generate && list.items.length > 0 && (
        <div className="mt-3" data-testid="ai-blockers">
          <Alert variant="info">
            <p className="font-medium">
              AI suggestions aren&apos;t available yet. Answers from your application data
              (salary, dates, contact details) and answering by hand still work.
            </p>
            <ul className="mt-1 list-disc pl-5">
              {list.ai.blockers.map((blocker) => (
                <li key={blocker.code}>{blocker.message}</li>
              ))}
            </ul>
          </Alert>
        </div>
      )}

      {list === null && !loadError && (
        <p className="mt-4 text-sm text-slate-500">Loading questions...</p>
      )}

      {list !== null && (
        <div className="mt-4 flex flex-col gap-4">
          {list.items.length === 0 && !adding && (
            <p className="text-sm text-slate-500">No questions yet.</p>
          )}
          {list.items.map((question) => (
            <QuestionCard
              key={question.id}
              applicationId={applicationId}
              question={question}
              onChange={replace}
              onDeleted={remove}
            />
          ))}

          {adding ? (
            <div className="rounded-lg border border-slate-200 p-4">
              {createError && (
                <div className="mb-3">
                  <Alert variant="error">{createError}</Alert>
                </div>
              )}
              <QuestionForm
                idPrefix="new-question"
                isSaving={isCreating}
                onSubmit={(body) => void create(body)}
                onCancel={() => {
                  setAdding(false);
                  setCreateError(null);
                }}
              />
            </div>
          ) : (
            <div>
              <Button variant="secondary" onClick={() => setAdding(true)}>
                Add question
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
