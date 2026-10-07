"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { remainingCharacters } from "@/lib/questions";
import type { ManualAnswerRequest, Question } from "@/types/questions";

interface Props {
  question: Question;
  initialText: string;
  initialOptions: string[];
  /** The user is editing the current suggestion rather than writing from scratch. */
  basedOnSuggestion: boolean;
  isSaving: boolean;
  onSave: (body: ManualAnswerRequest) => void;
  onCancel: () => void;
}

/** The labels of a yes/no question: its own two options, else Yes / No. */
export function yesNoOptions(question: Question): string[] {
  return question.options && question.options.length === 2 ? question.options : ["Yes", "No"];
}

/**
 * Where the user writes their own answer. It always works, with or without any AI;
 * what is saved is exactly what is typed (never shortened: over the limit, saving
 * is disabled and the counter says by how much).
 */
export function AnswerEditor({
  question,
  initialText,
  initialOptions,
  basedOnSuggestion,
  isSaving,
  onSave,
  onCancel,
}: Props) {
  const [text, setText] = useState(initialText);
  const [selected, setSelected] = useState<string[]>(initialOptions);
  const type = question.question_type;
  const isChoice = type === "SINGLE_CHOICE" || type === "MULTI_CHOICE";
  const isYesNo = type === "YES_NO";
  const inputId = `answer-${question.id}`;

  const remaining = remainingCharacters(question, text);
  const over = remaining !== null && remaining < 0;
  const empty = isChoice ? selected.length === 0 : text.trim().length === 0;

  function save() {
    if (isChoice) {
      onSave({ selected_options: selected, based_on_suggestion: basedOnSuggestion });
    } else {
      onSave({ answer: text, based_on_suggestion: basedOnSuggestion });
    }
  }

  function toggle(option: string) {
    if (type === "SINGLE_CHOICE") {
      setSelected([option]);
    } else {
      setSelected((current) =>
        current.includes(option) ? current.filter((o) => o !== option) : [...current, option]
      );
    }
  }

  return (
    <div className="mt-2 flex flex-col gap-2">
      {isChoice && (
        <fieldset>
          <legend className="sr-only">Your answer</legend>
          <div className="flex flex-col gap-1">
            {(question.options ?? []).map((option) => (
              <label key={option} className="flex items-center gap-2 text-sm text-slate-800">
                <input
                  type={type === "SINGLE_CHOICE" ? "radio" : "checkbox"}
                  name={`choice-${question.id}`}
                  checked={selected.includes(option)}
                  onChange={() => toggle(option)}
                />
                {option}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {isYesNo && (
        <fieldset>
          <legend className="sr-only">Your answer</legend>
          <div className="flex gap-4">
            {yesNoOptions(question).map((option) => (
              <label key={option} className="flex items-center gap-2 text-sm text-slate-800">
                <input
                  type="radio"
                  name={`yesno-${question.id}`}
                  checked={text === option}
                  onChange={() => setText(option)}
                />
                {option}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {!isChoice && !isYesNo && (
        <>
          <label htmlFor={inputId} className="sr-only">
            Your answer
          </label>
          {type === "FREE_TEXT" || type === "UNKNOWN" ? (
            <textarea
              id={inputId}
              rows={5}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              value={text}
              onChange={(event) => setText(event.target.value)}
              aria-invalid={over}
              aria-describedby={`${inputId}-count`}
            />
          ) : (
            <input
              id={inputId}
              type="text"
              inputMode={type === "DATE" ? "text" : "decimal"}
              placeholder={type === "DATE" ? "YYYY-MM-DD" : undefined}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              value={text}
              onChange={(event) => setText(event.target.value)}
              aria-invalid={over}
              aria-describedby={`${inputId}-count`}
            />
          )}
        </>
      )}

      {remaining !== null && !isChoice && (
        <p
          id={`${inputId}-count`}
          data-testid="character-counter"
          className={`text-xs ${over ? "font-medium text-red-700" : "text-slate-500"}`}
        >
          {over
            ? `${-remaining} characters over the limit of ${question.max_length}. Shorten your answer: JobPilot never cuts it for you.`
            : `${remaining} of ${question.max_length} characters remaining`}
        </p>
      )}

      <div className="flex gap-2">
        <Button isLoading={isSaving} disabled={over || empty} onClick={save}>
          Save
        </Button>
        <Button variant="secondary" disabled={isSaving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
