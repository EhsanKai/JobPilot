"use client";

import { useState } from "react";

import { DocumentPicker } from "@/components/applications/assistant/DocumentPicker";
import { Button } from "@/components/ui/Button";
import {
  DISPLAY_LABELS,
  DISPLAY_STYLES,
  INPUT_TYPE_LABELS,
  SOURCE_LABELS,
  intendedText,
  mappingLabel,
} from "@/lib/automation";
import type {
  DocumentChoice,
  FormField,
  MappingAction,
  QuestionBrief,
} from "@/types/automation";

interface Props {
  applicationId: string;
  sessionId: string;
  field: FormField;
  questions: QuestionBrief[];
  /** The assistant may not act (paused, in the user's control, ended). */
  locked: boolean;
  onMapping: (field: FormField, decision: MappingAction) => Promise<void>;
  onRefill: (field: FormField) => Promise<void>;
  onUseDocument: (field: FormField, choice: DocumentChoice) => Promise<void>;
  onClearDocument: (field: FormField) => Promise<void>;
}

/** One discovered form field: what it is, which question it maps to, what is approved. */
export function FieldRow({
  applicationId,
  sessionId,
  field,
  questions,
  locked,
  onMapping,
  onRefill,
  onUseDocument,
  onClearDocument,
}: Props) {
  const [changing, setChanging] = useState(false);
  const [target, setTarget] = useState("");
  const [confirmRefill, setConfirmRefill] = useState(false);
  const [busy, setBusy] = useState(false);

  async function decide(decision: MappingAction) {
    setBusy(true);
    try {
      await onMapping(field, decision);
      setChanging(false);
    } finally {
      setBusy(false);
    }
  }

  const isFile = field.input_type === "file";
  const hasQuestionMapping = !isFile && field.user_action_reason === null;
  const proposed = field.proposed_question;
  const answer = field.answer;
  const mappable = questions.filter((q) => q.id !== field.question?.id);

  return (
    <tr aria-label={`Field ${field.label}`} className={`align-top ${field.present ? "" : "opacity-60"}`}>
      <th scope="row" className="py-3 pr-3 text-left text-sm font-medium text-slate-900">
        <span>{field.label || "(no label)"}</span>
        {field.required && (
          <span className="ml-1 text-xs font-normal text-slate-500" title="Required by the form">
            Required
          </span>
        )}
        <span className="block text-xs font-normal text-slate-500">
          {INPUT_TYPE_LABELS[field.input_type] ?? field.input_type}
          {field.max_length ? ` · max ${field.max_length} characters` : ""}
          {!field.present ? " · not on the page now" : ""}
        </span>
      </th>

      <td className="py-3 pr-3 text-sm text-slate-700">
        {hasQuestionMapping ? (
          <>
            {field.question ? (
              <span>
                {field.question.question_text}
                <span className="block text-xs text-slate-500">{mappingLabel(field)}</span>
              </span>
            ) : proposed ? (
              <span>
                Proposed: {proposed.question_text}
                <span className="block text-xs text-slate-500">{mappingLabel(field)}</span>
              </span>
            ) : (
              <span className="text-slate-500">{mappingLabel(field)}</span>
            )}
            {!changing && (
              <div className="mt-1 flex flex-wrap gap-2">
                {field.mapping.status === "NEEDS_REVIEW" && proposed && (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={locked || busy}
                    onClick={() => void decide({ action: "confirm" })}
                    aria-label={`Confirm the question for ${field.label}`}
                  >
                    Confirm
                  </Button>
                )}
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => setChanging(true)}
                  aria-label={`Change the question for ${field.label}`}
                >
                  Change
                </Button>
                {field.mapping.status !== "UNMAPPED" && (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => void decide({ action: "unmap" })}
                    aria-label={`Leave ${field.label} unmapped`}
                  >
                    Leave unmapped
                  </Button>
                )}
                {field.mapping.status === "UNMAPPED" && (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => void decide({ action: "create" })}
                    aria-label={`Add a question for ${field.label}`}
                  >
                    Add as question
                  </Button>
                )}
              </div>
            )}
            {changing && (
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <select
                  aria-label={`Question for ${field.label}`}
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  className="max-w-xs rounded-lg border border-slate-300 px-2 py-1 text-sm"
                >
                  <option value="">Choose a question...</option>
                  {mappable.map((q) => (
                    <option key={q.id} value={q.id}>
                      {q.question_text}
                    </option>
                  ))}
                </select>
                <Button
                  type="button"
                  disabled={!target || busy}
                  onClick={() => void decide({ action: "change", question_id: target })}
                >
                  Use this question
                </Button>
                <Button type="button" variant="secondary" onClick={() => setChanging(false)}>
                  Cancel
                </Button>
              </div>
            )}
          </>
        ) : isFile ? (
          <span className="text-slate-500">A document</span>
        ) : (
          <span className="text-slate-500">Not a question</span>
        )}
      </td>

      <td className="py-3 text-sm">
        <span
          className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${DISPLAY_STYLES[field.display_status]}`}
        >
          {DISPLAY_LABELS[field.display_status]}
        </span>
        {field.display_message && (
          <p className="mt-1 text-xs text-slate-600">{field.display_message}</p>
        )}
        {answer && answer.state === "APPROVED" && (
          <p className="mt-1 text-xs text-slate-600">
            {field.display_status === "filled" ? "Filled" : "Will fill"} ({SOURCE_LABELS[answer.source!]}):{" "}
            <span className="font-medium text-slate-800">{intendedText(answer)}</span>
          </p>
        )}
        {answer && answer.state === "SUGGESTION_AVAILABLE" && (
          <p className="mt-1 text-xs text-slate-600">
            Suggestion (not filled until you accept it):{" "}
            <span className="text-slate-800">{intendedText(answer)}</span>
          </p>
        )}
        {field.display_status === "user_modified" && (
          <div className="mt-1">
            {!confirmRefill ? (
              <Button
                type="button"
                variant="secondary"
                disabled={locked}
                onClick={() => setConfirmRefill(true)}
                aria-label={`Fill ${field.label} again`}
              >
                Fill again
              </Button>
            ) : (
              <div role="group" aria-label={`Replace your change in ${field.label}`} className="text-xs">
                <p className="text-slate-700">Replace what you typed with the approved answer?</p>
                <div className="mt-1 flex gap-2">
                  <Button
                    type="button"
                    variant="danger"
                    onClick={() => {
                      setConfirmRefill(false);
                      void onRefill(field);
                    }}
                  >
                    Replace
                  </Button>
                  <Button type="button" variant="secondary" onClick={() => setConfirmRefill(false)}>
                    Keep mine
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
        {isFile && field.document && (
          <DocumentPicker
            applicationId={applicationId}
            sessionId={sessionId}
            field={field}
            disabled={false}
            onUse={onUseDocument}
            onClear={onClearDocument}
          />
        )}
      </td>
    </tr>
  );
}
