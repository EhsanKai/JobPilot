"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { apiMessage } from "@/lib/automation";
import { listDocumentChoices } from "@/lib/api/automation";
import type { DocumentChoice, FormField } from "@/types/automation";

interface Props {
  applicationId: string;
  sessionId: string;
  field: FormField;
  disabled: boolean;
  onUse: (field: FormField, choice: DocumentChoice) => Promise<void>;
  onClear: (field: FormField) => Promise<void>;
}

const ASKS: Record<string, string> = {
  tailored_cv: "Resume / CV",
  cover_letter: "Cover letter",
};

/**
 * Which file goes into a file field. JobPilot proposes the current version of the
 * document the field clearly asks for, but attaches nothing until the user presses
 * "Use this file" -- and then exactly that version, never a newer one by itself.
 */
export function DocumentPicker({ applicationId, sessionId, field, disabled, onUse, onClear }: Props) {
  const document = field.document!;
  const [choosing, setChoosing] = useState(false);
  const [choices, setChoices] = useState<DocumentChoice[] | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function openChooser() {
    setChoosing(true);
    setError(null);
    try {
      const loaded = await listDocumentChoices(applicationId, sessionId, field.id);
      setChoices(loaded.items);
      setSelectedId(loaded.items[0]?.id ?? "");
    } catch (e) {
      setError(apiMessage(e, "Unable to load your documents."));
    }
  }

  async function use(choice: DocumentChoice) {
    setBusy(true);
    setError(null);
    try {
      await onUse(field, choice);
      setChoosing(false);
    } catch (e) {
      setError(apiMessage(e, "Unable to choose this file."));
    } finally {
      setBusy(false);
    }
  }

  const shown = document.selected ?? document.proposed;
  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
      {document.detected_type ? (
        <p className="text-slate-700">
          This field appears to request: <strong>{ASKS[document.detected_type]}</strong>
        </p>
      ) : (
        <p className="text-slate-700">
          It is not clear which document this field asks for. Choose one yourself.
        </p>
      )}
      {shown && (
        <p className="mt-1 text-slate-700">
          {document.confirmed ? "Chosen:" : "Selected:"} <strong>{shown.label}</strong>
        </p>
      )}
      {document.confirmed && document.selected && (
        <p className="mt-1 text-xs text-slate-500">
          This exact version is attached ({document.selected.file_name}).
        </p>
      )}
      {document.newer_available && document.selected && (
        <p className="mt-1 text-xs text-slate-600">
          A newer version exists, but your choice stays {document.selected.label}.
        </p>
      )}
      {!document.format_accepted && (
        <p role="alert" className="mt-1 text-xs text-amber-800">
          This form may not accept this kind of file (JobPilot attaches DOCX, or your uploaded
          resume as it is). You can attach a file yourself in the browser.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {error}
        </p>
      )}
      {!choosing && (
        <div className="mt-2 flex flex-wrap gap-2">
          {!document.confirmed && document.proposed && (
            <Button
              type="button"
              variant="secondary"
              disabled={disabled || busy}
              onClick={() => void use(document.proposed!)}
              aria-label={`Use this file for ${field.label}`}
            >
              Use this file
            </Button>
          )}
          <Button
            type="button"
            variant="secondary"
            disabled={disabled || busy}
            onClick={() => void openChooser()}
            aria-label={`Choose another file for ${field.label}`}
          >
            Choose another
          </Button>
          {document.confirmed && (
            <Button
              type="button"
              variant="secondary"
              disabled={disabled || busy}
              onClick={() => void onClear(field)}
              aria-label={`Remove the chosen file for ${field.label}`}
            >
              Don&apos;t attach
            </Button>
          )}
        </div>
      )}
      {choosing && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {choices === null && !error && <span className="text-xs text-slate-500">Loading...</span>}
          {choices !== null && choices.length === 0 && (
            <span className="text-xs text-slate-600">
              There is no document to attach yet. Create your CV or Anschreiben above.
            </span>
          )}
          {choices !== null && choices.length > 0 && (
            <>
              <select
                aria-label={`Document for ${field.label}`}
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                className="rounded-lg border border-slate-300 px-2 py-1 text-sm"
              >
                {choices.map((choice) => (
                  <option key={`${choice.type}-${choice.id}`} value={choice.id}>
                    {choice.label}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                disabled={busy || !selectedId}
                onClick={() => {
                  const choice = choices.find((c) => c.id === selectedId);
                  if (choice) void use(choice);
                }}
                aria-label={`Use the selected file for ${field.label}`}
              >
                Use this file
              </Button>
            </>
          )}
          <Button type="button" variant="secondary" onClick={() => setChoosing(false)}>
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}
