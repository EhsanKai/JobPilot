"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { DocumentEditor } from "@/components/applications/DocumentEditor";
import { DocumentViewer } from "@/components/applications/DocumentViewer";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { generateDocument, getDocument, listDocuments } from "@/lib/api/documents";
import {
  editDocument,
  generateOverEdits,
  humanizeLetter,
  loadPhoto,
  removePhoto,
  uploadPhoto,
} from "@/lib/api/documentActions";
import { copyText } from "@/lib/questions";
import { documentToText, downloadText } from "@/lib/documentText";
import {
  DOCUMENT_TITLES,
  DOCUMENT_TYPES,
  GENERATE_LABELS,
  attemptsOf,
  currentDocument,
  describeGenerated,
  isGenerating,
  languageName,
  pendingAttempt,
  problemFromApi,
  violationLabel,
  type GenerationProblem,
} from "@/lib/documents";
import { ApiError } from "@/lib/http";
import type {
  DocumentEditRequest,
  DocumentList,
  DocumentSummary,
  DocumentType,
  GeneratedDocument,
} from "@/types/documents";

/** How often a generation that is running on the server is checked, and for how long. */
const DEFAULT_POLL_MS = 3000;
const MAX_POLLS = 100;

interface Props {
  applicationId: string;
  /** The application's saved language (`de` / `en`), or null when none is chosen. */
  language: string | null;
  /** Changes whenever the application is saved: what documents may need is then re-read. */
  reloadKey: string;
  /** Unsaved edits in the form: documents use the *saved* application data. */
  hasUnsavedChanges: boolean;
  pollIntervalMs?: number;
}

/**
 * Tailored documents for one application: a CV and an Anschreiben, each
 * generated on request, validated against the confirmed resume, and kept
 * as numbered versions. A document only appears here after it passed
 * validation; a rejected or failed attempt is shown as an attempt, never as a
 * document.
 */
export function ApplicationDocuments({
  applicationId,
  language,
  reloadKey,
  hasUnsavedChanges,
  pollIntervalMs = DEFAULT_POLL_MS,
}: Props) {
  const [list, setList] = useState<DocumentList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [generating, setGenerating] = useState<Partial<Record<DocumentType, boolean>>>({});
  const [problems, setProblems] = useState<Partial<Record<DocumentType, GenerationProblem>>>({});
  const [viewing, setViewing] = useState<Partial<Record<DocumentType, GeneratedDocument>>>({});
  const [viewLoading, setViewLoading] = useState<string | null>(null);
  const [viewError, setViewError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  const [humanizing, setHumanizing] = useState(false);
  const [editing, setEditing] = useState<Partial<Record<DocumentType, boolean>>>({});
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const isMounted = useRef(true);
  const polls = useRef(0);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const loaded = await listDocuments(applicationId);
      if (!isMounted.current) return;
      setList(loaded);
      setLoadError(null);
    } catch {
      if (!isMounted.current) return;
      setLoadError("Unable to load your documents. Please try again.");
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  // The user's own photo (V0.9): fetched as a file with their credentials and shown
  // on the CV. It is never part of any generated document.
  const photoStamp = list?.photo?.updated_at ?? null;
  useEffect(() => {
    if (photoStamp === null) {
      setPhotoUrl(null);
      return;
    }
    let url: string | null = null;
    let cancelled = false;
    void loadPhoto(applicationId)
      .then((blob) => {
        if (cancelled || blob === null || typeof URL.createObjectURL !== "function") return;
        url = URL.createObjectURL(blob);
        setPhotoUrl(url);
      })
      .catch(() => {
        if (!cancelled) setPhotoUrl(null);
      });
    return () => {
      cancelled = true;
      if (url && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(url);
    };
  }, [applicationId, photoStamp]);

  // A generation that is running on the server (another tab, a reload) is
  // followed until it ends; the server itself fails one that was abandoned,
  // so this never waits forever.
  const serverGenerating = list !== null && isGenerating(list.items);
  const busy = Object.values(generating).some(Boolean);
  useEffect(() => {
    if (!serverGenerating || busy) {
      polls.current = 0;
      setStalled(false);
      return;
    }
    if (polls.current >= MAX_POLLS) {
      setStalled(true);
      return;
    }
    const timer = setTimeout(() => {
      polls.current += 1;
      void load();
    }, pollIntervalMs);
    return () => clearTimeout(timer);
  }, [serverGenerating, busy, list, load, pollIntervalMs]);

  async function generate(type: DocumentType, overEdits = false) {
    setGenerating((current) => ({ ...current, [type]: true }));
    setProblems((current) => ({ ...current, [type]: undefined }));
    setViewing((current) => ({ ...current, [type]: undefined }));
    setEditing((current) => ({ ...current, [type]: false }));
    try {
      if (overEdits) await generateOverEdits(applicationId, type);
      else await generateDocument(applicationId, type);
    } catch (err) {
      const problem: GenerationProblem =
        err instanceof ApiError
          ? problemFromApi(err)
          : {
              kind: "failed",
              message: "Generation failed. Please try again.",
              violations: [],
            };
      if (isMounted.current) setProblems((current) => ({ ...current, [type]: problem }));
    } finally {
      if (isMounted.current) setGenerating((current) => ({ ...current, [type]: false }));
    }
    // Every attempt -- also a rejected one -- is part of the history.
    await load();
  }

  async function view(summary: DocumentSummary) {
    setViewError(null);
    setViewLoading(summary.id);
    try {
      const document = await getDocument(applicationId, summary.id);
      if (isMounted.current) {
        setViewing((current) => ({ ...current, [summary.document_type]: document }));
      }
    } catch {
      if (isMounted.current) setViewError("Unable to open this document. Please try again.");
    } finally {
      if (isMounted.current) setViewLoading(null);
    }
  }

  function hide(type: DocumentType) {
    setViewing((current) => ({ ...current, [type]: undefined }));
    setEditing((current) => ({ ...current, [type]: false }));
  }

  /** Humanize: a NEW version with a more natural tone; the current one stays as it is. */
  async function humanize(overEdits: boolean) {
    setHumanizing(true);
    setProblems((current) => ({ ...current, cover_letter: undefined }));
    try {
      const created = await humanizeLetter(applicationId, {
        acknowledgeEditedCurrent: overEdits,
      });
      if (isMounted.current) {
        setViewing((current) => ({ ...current, cover_letter: created }));
      }
    } catch (err) {
      const problem: GenerationProblem =
        err instanceof ApiError
          ? problemFromApi(err)
          : { kind: "failed", message: "Humanize failed. Please try again.", violations: [] };
      if (isMounted.current) setProblems((current) => ({ ...current, cover_letter: problem }));
    } finally {
      if (isMounted.current) setHumanizing(false);
    }
    await load();
  }

  async function saveEdit(document: GeneratedDocument, edit: DocumentEditRequest) {
    setSavingEdit(true);
    setEditError(null);
    try {
      const created = await editDocument(applicationId, document.id, edit);
      if (!isMounted.current) return;
      setViewing((current) => ({ ...current, [created.document_type]: created }));
      setEditing((current) => ({ ...current, [created.document_type]: false }));
      await load();
    } catch (err) {
      if (isMounted.current) {
        setEditError(
          err instanceof ApiError ? err.message : "Unable to save your edit. Please try again."
        );
      }
    } finally {
      if (isMounted.current) setSavingEdit(false);
    }
  }

  async function addPhoto(file: File) {
    setPhotoBusy(true);
    setPhotoError(null);
    try {
      await uploadPhoto(applicationId, file);
      await load();
    } catch (err) {
      if (isMounted.current) {
        setPhotoError(
          err instanceof ApiError ? err.message : "Unable to upload the photo. Please try again."
        );
      }
    } finally {
      if (isMounted.current) setPhotoBusy(false);
    }
  }

  async function deletePhoto() {
    setPhotoBusy(true);
    setPhotoError(null);
    try {
      await removePhoto(applicationId);
      await load();
    } catch {
      if (isMounted.current) setPhotoError("Unable to remove the photo. Please try again.");
    } finally {
      if (isMounted.current) setPhotoBusy(false);
    }
  }

  return (
    <Card aria-labelledby="documents-heading" id="documents">
      <h2 id="documents-heading" className="text-lg font-semibold text-slate-900">
        Documents
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Tailored for this job from your confirmed resume
        {language ? ` and written in ${languageName(language)}` : ""}. Every statement is checked
        against your resume before a document is saved: nothing is invented. Regenerating creates a
        new version and keeps the old ones.
      </p>

      {loadError && (
        <div className="mt-3">
          <Alert variant="error">{loadError}</Alert>
        </div>
      )}
      {viewError && (
        <div className="mt-3">
          <Alert variant="error">{viewError}</Alert>
        </div>
      )}
      {hasUnsavedChanges && (
        <div className="mt-3">
          <Alert variant="info">
            You have unsaved changes. Documents use your saved application data, so save them
            before generating.
          </Alert>
        </div>
      )}
      {stalled && (
        <div className="mt-3">
          <Alert variant="info">
            A generation is taking unusually long. Reload the page to check on it again.
          </Alert>
        </div>
      )}

      {list === null && !loadError && (
        <p className="mt-4 text-sm text-slate-500">Loading documents...</p>
      )}

      {list !== null && (
        <div className="mt-4 flex flex-col gap-6">
          {DOCUMENT_TYPES.map((type) => (
            <DocumentPanel
              key={type}
              type={type}
              list={list}
              isGenerating={Boolean(generating[type])}
              problem={problems[type]}
              viewing={viewing[type]}
              viewLoading={viewLoading}
              hasUnsavedChanges={hasUnsavedChanges}
              applicationId={applicationId}
              onGenerate={(overEdits) => void generate(type, overEdits)}
              onView={(summary) => void view(summary)}
              onHide={() => hide(type)}
              humanizing={type === "cover_letter" && humanizing}
              onHumanize={(overEdits) => void humanize(overEdits)}
              isEditing={Boolean(editing[type])}
              onEdit={(value) => {
                setEditError(null);
                setEditing((current) => ({ ...current, [type]: value }));
              }}
              isSavingEdit={savingEdit}
              editError={editError}
              onSaveEdit={(document, edit) => void saveEdit(document, edit)}
              photoUrl={photoUrl}
              photo={list.photo ?? null}
              photoBusy={photoBusy}
              photoError={photoError}
              onAddPhoto={(file) => void addPhoto(file)}
              onRemovePhoto={() => void deletePhoto()}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

interface PanelProps {
  type: DocumentType;
  list: DocumentList;
  isGenerating: boolean;
  problem: GenerationProblem | undefined;
  viewing: GeneratedDocument | undefined;
  viewLoading: string | null;
  hasUnsavedChanges: boolean;
  applicationId: string;
  /** `overEdits`: the user confirmed replacing a current version that holds their edits. */
  onGenerate: (overEdits: boolean) => void;
  onView: (summary: DocumentSummary) => void;
  onHide: () => void;
  humanizing: boolean;
  onHumanize: (overEdits: boolean) => void;
  isEditing: boolean;
  onEdit: (value: boolean) => void;
  isSavingEdit: boolean;
  editError: string | null;
  onSaveEdit: (document: GeneratedDocument, edit: DocumentEditRequest) => void;
  photoUrl: string | null;
  photo: DocumentList["photo"];
  photoBusy: boolean;
  photoError: string | null;
  onAddPhoto: (file: File) => void;
  onRemovePhoto: () => void;
}

type PendingConfirm = "regenerate" | "humanize" | null;

function DocumentPanel({
  type,
  list,
  isGenerating: generatingNow,
  problem,
  viewing,
  viewLoading,
  hasUnsavedChanges,
  applicationId,
  onGenerate,
  onView,
  onHide,
  humanizing,
  onHumanize,
  isEditing,
  onEdit,
  isSavingEdit,
  editError,
  onSaveEdit,
  photoUrl,
  photo,
  photoBusy,
  photoError,
  onAddPhoto,
  onRemovePhoto,
}: PanelProps) {
  const title = DOCUMENT_TITLES[type];
  const readiness = list[type];
  const current = currentDocument(list.items, type);
  const attempt = pendingAttempt(list.items, type);
  const inFlight = generatingNow || humanizing || attempt?.status === "generating";
  const attempts = attemptsOf(list.items, type);
  const headingId = `documents-${type}-heading`;
  const blocked = !readiness.can_generate;
  const canGenerate = !blocked && !hasUnsavedChanges && !inFlight;
  const [confirm, setConfirm] = useState<PendingConfirm>(null);
  const [copied, setCopied] = useState<boolean | null>(null);
  // The current version holds the user's own edits: replacing it takes a confirmation.
  const currentIsEdited = current?.origin === "user_edited";

  function regenerate() {
    if (currentIsEdited) setConfirm("regenerate");
    else onGenerate(false);
  }

  function humanize() {
    // Humanize always asks: it makes a new version, and says so.
    setConfirm("humanize");
  }

  async function copyDocument(document: GeneratedDocument) {
    if (document.content === null) return;
    const ok = await copyText(documentToText(document.content));
    setCopied(ok);
    setTimeout(() => setCopied(null), 2500);
  }

  return (
    <section aria-labelledby={headingId} data-document-type={type} className="rounded-lg border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id={headingId} className="text-base font-semibold text-slate-900">
            {title}
          </h3>
          <DocumentStatus
            current={current}
            attempt={attempt}
            inFlight={inFlight}
            generatingNow={generatingNow || humanizing}
            humanizing={humanizing}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {current ? (
            <>
              <Button
                variant="secondary"
                aria-label={`View ${title}`}
                isLoading={viewLoading === current.id}
                disabled={Boolean(viewing && viewing.id === current.id)}
                onClick={() => onView(current)}
              >
                View
              </Button>
              {type === "cover_letter" && (
                <Button
                  variant="secondary"
                  aria-label="Humanize cover letter"
                  isLoading={humanizing}
                  disabled={inFlight || confirm === "humanize"}
                  onClick={humanize}
                >
                  {humanizing ? "Humanizing..." : "✨ Humanize"}
                </Button>
              )}
              <Button
                variant="secondary"
                aria-label={`Regenerate ${title}`}
                isLoading={generatingNow}
                disabled={!canGenerate}
                onClick={regenerate}
              >
                {generatingNow ? "Generating..." : "Regenerate"}
              </Button>
            </>
          ) : (
            <Button isLoading={generatingNow} disabled={!canGenerate} onClick={() => onGenerate(false)}>
              {generatingNow ? "Generating..." : GENERATE_LABELS[type]}
            </Button>
          )}
        </div>
      </div>

      {confirm === "humanize" && (
        <div role="group" aria-label="Confirm humanize" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p>Humanize this cover letter while preserving all factual content?</p>
          <p className="mt-1 text-xs">
            It creates a <strong>new version</strong>: your current version stays untouched in the
            history, and nothing the letter states is added or removed. The reworded letter is
            checked against your resume like any generated one; if it fails, nothing changes.
            {currentIsEdited &&
              " Your current version contains your own edits: the new version will replace it as the current one, and yours stays in the history."}
          </p>
          <div className="mt-2 flex gap-2">
            <Button
              disabled={inFlight}
              onClick={() => {
                setConfirm(null);
                onHumanize(currentIsEdited);
              }}
            >
              Humanize
            </Button>
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {confirm === "regenerate" && (
        <div role="group" aria-label="Confirm regenerate" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p>
            Your current version contains your own edits. Generating a new version does not delete
            them (they stay in the history), but the new version will replace them as the current
            one. Generate a new version anyway?
          </p>
          <div className="mt-2 flex gap-2">
            <Button
              onClick={() => {
                setConfirm(null);
                onGenerate(true);
              }}
            >
              Generate new version
            </Button>
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {blocked && (
        <div className="mt-3" data-testid={`${type}-blockers`}>
          <Alert variant="info">
            <p className="font-medium">
              {readiness.blockers.some((b) => b.code.startsWith("SALARY_"))
                ? "A salary decision is needed first."
                : "Before you can generate this:"}
            </p>
            <ul className="mt-1 list-disc pl-5">
              {readiness.blockers.map((blocker) => (
                <li key={blocker.code}>{blocker.message}</li>
              ))}
            </ul>
            <p className="mt-2">
              <a href="#preparation-heading" className="font-medium text-brand-700 underline">
                Go to Application preparation
              </a>
            </p>
          </Alert>
        </div>
      )}

      {problem && <GenerationProblemView problem={problem} />}

      {type === "tailored_cv" && (
        <CvPhotoControl
          photo={photo}
          photoUrl={photoUrl}
          busy={photoBusy}
          error={photoError}
          onAdd={onAddPhoto}
          onRemove={onRemovePhoto}
        />
      )}

      {viewing && !isEditing && (
        <div>
          <DocumentViewer document={viewing} title={title} photoUrl={photoUrl} />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button variant="secondary" aria-label={`Edit ${title}`} onClick={() => onEdit(true)}>
              Edit
            </Button>
            <Button
              variant="secondary"
              aria-label={`Copy ${title}`}
              onClick={() => void copyDocument(viewing)}
            >
              Copy
            </Button>
            <Button
              variant="secondary"
              aria-label={`Download ${title}`}
              onClick={() =>
                viewing.content &&
                downloadText(
                  `${type === "tailored_cv" ? "cv" : "anschreiben"}-v${viewing.version_number}.txt`,
                  documentToText(viewing.content)
                )
              }
            >
              Download .txt
            </Button>
            <Button variant="secondary" aria-label={`Hide ${title}`} onClick={onHide}>
              Hide
            </Button>
            {copied !== null && (
              <span role="status" className="text-xs text-slate-500">
                {copied ? "Copied" : "Copy failed: select the text and copy it yourself."}
              </span>
            )}
          </div>
        </div>
      )}

      {viewing && isEditing && (
        <DocumentEditor
          document={viewing}
          title={title}
          isSaving={isSavingEdit}
          error={editError}
          onSave={(edit) => onSaveEdit(viewing, edit)}
          onCancel={() => onEdit(false)}
        />
      )}

      {attempts.length > 0 && (
        <History
          title={title}
          attempts={attempts}
          applicationId={applicationId}
          viewing={viewing}
          viewLoading={viewLoading}
          onView={onView}
        />
      )}
    </section>
  );
}

/**
 * The user's own photo for the CV. It is theirs: JobPilot never generates,
 * changes or replaces it, and regenerating or humanizing a document leaves it
 * as it is.
 */
function CvPhotoControl({
  photo,
  photoUrl,
  busy,
  error,
  onAdd,
  onRemove,
}: {
  photo: DocumentList["photo"];
  photoUrl: string | null;
  busy: boolean;
  error: string | null;
  onAdd: (file: File) => void;
  onRemove: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg bg-slate-50 p-3" aria-label="CV photo" role="group">
      {photoUrl && (
        // eslint-disable-next-line @next/next/no-img-element -- a blob URL of the user's own photo
        <img src={photoUrl} alt="Your CV photo" className="h-14 w-14 rounded-lg object-cover" />
      )}
      <div className="text-sm text-slate-600">
        <p className="font-medium text-slate-800">{photo ? "Your photo" : "No photo"}</p>
        <p className="text-xs text-slate-500">
          Optional and entirely yours: JobPilot never creates, changes or replaces it. JPEG or PNG,
          up to 2 MB.
        </p>
      </div>
      <div className="flex gap-2">
        <input
          ref={input}
          type="file"
          accept="image/jpeg,image/png"
          className="sr-only"
          aria-label="Choose photo file"
          data-testid="photo-file"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onAdd(file);
            event.target.value = "";
          }}
        />
        <Button variant="secondary" isLoading={busy} onClick={() => input.current?.click()}>
          {photo ? "Replace photo" : "Add photo"}
        </Button>
        {photo && (
          <Button variant="secondary" disabled={busy} onClick={onRemove}>
            Remove photo
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="w-full text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

function DocumentStatus({
  current,
  attempt,
  inFlight,
  generatingNow,
  humanizing,
}: {
  current: DocumentSummary | null;
  attempt: DocumentSummary | null;
  inFlight: boolean;
  generatingNow: boolean;
  humanizing: boolean;
}) {
  if (inFlight) {
    return (
      <p className="mt-1 flex items-center gap-2 text-sm text-slate-600" role="status">
        <span
          className="h-3 w-3 animate-spin rounded-full border-2 border-brand-600 border-t-transparent"
          aria-hidden="true"
        />
        {humanizing ? "Humanizing" : "Generating"}...{" "}
        {generatingNow ? "this can take a minute." : "started in another window."}
      </p>
    );
  }
  if (!current) {
    return (
      <p className="mt-1 text-sm text-slate-600" data-status="not_generated">
        Status: Not generated
        {attempt?.status === "rejected" && " — the last attempt was rejected"}
        {attempt?.status === "failed" && " — the last attempt failed"}
      </p>
    );
  }
  const generated = describeGenerated(current.completed_at ?? current.created_at);
  return (
    <div className="mt-1 text-sm text-slate-600" data-status="generated">
      <p>
        <span className="font-medium text-emerald-700">✓ Generated</span>
        {" · "}Version {current.version_number}
        {generated ? ` · Generated ${generated}` : ""}
      </p>
      {current.origin === "user_edited" ? (
        <p className="text-xs text-amber-700">
          ✎ Edited by you — your own words, not checked by JobPilot
        </p>
      ) : (
        <p className="text-xs text-slate-500">
          ✓ Checked against your resume
          {current.resume_version_number !== null
            ? ` (resume version ${current.resume_version_number})`
            : ""}
          {current.origin === "humanized" ? " · reworded by Humanize" : ""}
        </p>
      )}
      {current.is_outdated && (
        <p className="mt-1 text-xs font-medium text-amber-700">
          Outdated: your resume or application data changed since this was generated. Regenerate to
          bring it up to date.
        </p>
      )}
      {attempt && attempt.status === "rejected" && (
        <p className="mt-1 text-xs text-red-700">
          The latest attempt was rejected, so this version is still the current one.
        </p>
      )}
      {attempt && attempt.status === "failed" && (
        <p className="mt-1 text-xs text-red-700">
          The latest attempt failed, so this version is still the current one.
        </p>
      )}
    </div>
  );
}

function GenerationProblemView({ problem }: { problem: GenerationProblem }) {
  if (problem.kind === "blocked") {
    return (
      <div className="mt-3">
        <Alert variant="info">{problem.message}</Alert>
      </div>
    );
  }
  return (
    <div className="mt-3">
      <Alert variant="error">
        <p>{problem.message}</p>
        {problem.kind === "rejected" && problem.violations.length > 0 && (
          <ul className="mt-2 list-disc pl-5" aria-label="Why it was rejected">
            {problem.violations.map((violation, index) => (
              <li key={index}>
                {violationLabel(violation)}
                {violation.excerpt ? `: “${violation.excerpt}”` : ""}
              </li>
            ))}
          </ul>
        )}
      </Alert>
    </div>
  );
}

function History({
  title,
  attempts,
  applicationId,
  viewing,
  viewLoading,
  onView,
}: {
  title: string;
  attempts: DocumentSummary[];
  applicationId: string;
  viewing: GeneratedDocument | undefined;
  viewLoading: string | null;
  onView: (summary: DocumentSummary) => void;
}) {
  const [reasons, setReasons] = useState<Record<string, string[]>>({});

  async function showReasons(summary: DocumentSummary) {
    try {
      const detail = await getDocument(applicationId, summary.id);
      setReasons((current) => ({
        ...current,
        [summary.id]: (detail.validation?.violations ?? []).map(
          (violation) =>
            `${violationLabel(violation)}${violation.excerpt ? `: “${violation.excerpt}”` : ""}`
        ),
      }));
    } catch {
      setReasons((current) => ({ ...current, [summary.id]: ["Unable to load the reasons."] }));
    }
  }

  return (
    <details className="mt-3 text-sm" aria-label={`${title} history`}>
      <summary className="cursor-pointer select-none text-slate-600 hover:text-slate-900">
        History ({attempts.length} {attempts.length === 1 ? "attempt" : "attempts"})
      </summary>
      <ul className="mt-2 flex flex-col gap-2">
        {attempts.map((attempt) => (
          <li
            key={attempt.id}
            data-status={attempt.status}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2"
          >
            <span className="text-slate-700">
              {attempt.status === "validated" && (
                <>
                  Version {attempt.version_number}
                  {attempt.is_current ? " (current)" : ""} ·{" "}
                  {attempt.origin === "user_edited" ? "edited by you" : "validated"}
                  {attempt.origin === "humanized" ? " · humanized" : ""}
                </>
              )}
              {attempt.status === "rejected" && (
                <>
                  Rejected · {attempt.violation_count}{" "}
                  {attempt.violation_count === 1 ? "unsupported claim" : "unsupported claims"} ·
                  nothing saved
                </>
              )}
              {attempt.status === "failed" && (
                <>Failed · {attempt.error_message ?? "Generation failed."}</>
              )}
              {attempt.status === "generating" && <>Generating...</>}
              {" · "}
              {describeGenerated(attempt.created_at)}
            </span>
            {attempt.status === "validated" && (
              <Button
                variant="secondary"
                aria-label={`View ${title} version ${attempt.version_number}`}
                isLoading={viewLoading === attempt.id}
                disabled={Boolean(viewing && viewing.id === attempt.id)}
                onClick={() => onView(attempt)}
              >
                View
              </Button>
            )}
            {attempt.status === "rejected" && !reasons[attempt.id] && (
              <Button
                variant="secondary"
                aria-label={`Show why attempt was rejected`}
                onClick={() => void showReasons(attempt)}
              >
                Show why
              </Button>
            )}
            {reasons[attempt.id] && (
              <ul className="w-full list-disc pl-5 text-xs text-red-700">
                {reasons[attempt.id]?.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
