"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { TYPE_LABELS, takesOptions } from "@/lib/questions";
import { QUESTION_TYPES } from "@/types/questions";
import type { Question, QuestionCreateRequest, QuestionType } from "@/types/questions";

interface Props {
  /** Present when editing an existing question. */
  question?: Question;
  idPrefix: string;
  isSaving: boolean;
  onSubmit: (body: QuestionCreateRequest) => void;
  onCancel: () => void;
}

/** Add or edit a question the employer asks. Entered by the user in V0.9. */
export function QuestionForm({ question, idPrefix, isSaving, onSubmit, onCancel }: Props) {
  const [text, setText] = useState(question?.question_text ?? "");
  const [type, setType] = useState<QuestionType>(question?.question_type ?? "FREE_TEXT");
  const [required, setRequired] = useState(question?.required ?? false);
  const [maxLength, setMaxLength] = useState(
    question?.max_length != null ? String(question.max_length) : ""
  );
  const [options, setOptions] = useState((question?.options ?? []).join("\n"));
  const [problem, setProblem] = useState<string | null>(null);

  const parsedOptions = options
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim()) {
      setProblem("Enter the question.");
      return;
    }
    const limit = maxLength.trim() === "" ? null : Number(maxLength);
    if (limit !== null && (!Number.isInteger(limit) || limit < 1)) {
      setProblem("The character limit must be a whole number of at least 1.");
      return;
    }
    const isChoice = type === "SINGLE_CHOICE" || type === "MULTI_CHOICE";
    if (isChoice && parsedOptions.length === 0) {
      setProblem("A choice question needs at least one option (one per line).");
      return;
    }
    if (type === "YES_NO" && parsedOptions.length !== 0 && parsedOptions.length !== 2) {
      setProblem("Give two options (yes label, then no label) or none.");
      return;
    }
    setProblem(null);
    onSubmit({
      question_text: text.trim(),
      question_type: type,
      required,
      max_length: limit,
      options: takesOptions(type) && parsedOptions.length > 0 ? parsedOptions : null,
    });
  }

  const field = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900";

  return (
    <form
      onSubmit={submit}
      noValidate
      className="flex flex-col gap-3"
      aria-label={question ? "Edit question" : "Add question"}
    >
      <div>
        <label htmlFor={`${idPrefix}-text`} className="text-sm font-medium text-slate-700">
          Question
        </label>
        <textarea
          id={`${idPrefix}-text`}
          rows={2}
          className={field}
          value={text}
          maxLength={2000}
          onChange={(event) => setText(event.target.value)}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label htmlFor={`${idPrefix}-type`} className="text-sm font-medium text-slate-700">
            Answer type
          </label>
          <select
            id={`${idPrefix}-type`}
            className={field}
            value={type}
            onChange={(event) => setType(event.target.value as QuestionType)}
          >
            {QUESTION_TYPES.map((value) => (
              <option key={value} value={value}>
                {TYPE_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-max`} className="text-sm font-medium text-slate-700">
            Max characters
          </label>
          <input
            id={`${idPrefix}-max`}
            type="number"
            min={1}
            className={field}
            value={maxLength}
            placeholder="No limit"
            onChange={(event) => setMaxLength(event.target.value)}
          />
        </div>
        <label className="flex items-center gap-2 self-end pb-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={required}
            onChange={(event) => setRequired(event.target.checked)}
          />
          Required
        </label>
      </div>
      {takesOptions(type) && (
        <div>
          <label htmlFor={`${idPrefix}-options`} className="text-sm font-medium text-slate-700">
            Options (one per line)
          </label>
          <textarea
            id={`${idPrefix}-options`}
            rows={3}
            className={field}
            value={options}
            onChange={(event) => setOptions(event.target.value)}
          />
          {type === "YES_NO" && (
            <p className="mt-1 text-xs text-slate-500">
              Optional: the form&apos;s own labels, yes first and then no.
            </p>
          )}
        </div>
      )}
      {problem && (
        <p role="alert" className="text-sm text-red-700">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" isLoading={isSaving}>
          {question ? "Save question" : "Add question"}
        </Button>
        <Button type="button" variant="secondary" disabled={isSaving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
