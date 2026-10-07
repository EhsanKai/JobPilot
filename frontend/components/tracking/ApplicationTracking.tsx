"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { ApplicationTimeline } from "@/components/tracking/ApplicationTimeline";
import { StatusBadge } from "@/components/tracking/StatusBadge";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { getApplication } from "@/lib/api/applications";
import { changeStatus, getTimeline, updateTracking } from "@/lib/api/tracking";
import { ApiError } from "@/lib/http";
import { safeExternalUrl, sourceLabel } from "@/lib/jobs";
import {
  ALL_STATUSES,
  STATUS_PRESENTATION,
  dateText,
  fromDateTimeLocal,
  statusLabel,
  submissionText,
  toDateTimeLocal,
  trackingErrorMessage,
} from "@/lib/tracking";
import type {
  Application,
  ApplicationStatus,
  ApplicationTracking as Tracking,
  StatusChangeRequest,
  StatusChangeResponse,
  TimelineEntry,
  TrackingUpdateRequest,
} from "@/types/application";

const SELECT_CLASS =
  "rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500";
const TEXTAREA_CLASS =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500";
const MAX_NOTE_LENGTH = 2000;

/** Statuses in which the user is waiting on the employer: a follow-up date makes sense. */
function acceptsFollowUp(status: ApplicationStatus | ""): boolean {
  return status === "APPLIED" || status === "FOLLOW_UP" || status === "INTERVIEW";
}

/**
 * One application's tracking page: where it stands, what happened, and what the user can do
 * about it. Every action is the user's own -- JobPilot records it and sends nothing to anyone.
 * The server decides which changes are allowed; this only offers what it says is allowed.
 */
export function ApplicationTracking({ applicationId }: { applicationId: string }) {
  const [application, setApplication] = useState<Application | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);

  const loadTimeline = useCallback(async () => {
    try {
      const loaded = await getTimeline(applicationId);
      setTimeline(loaded.items);
      setTimelineError(null);
    } catch {
      setTimelineError("Unable to load the timeline. Please try again.");
    }
  }, [applicationId]);

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    setLoadError(null);
    Promise.all([getApplication(applicationId), getTimeline(applicationId)])
      .then(([loaded, history]) => {
        if (!isMounted) return;
        setApplication(loaded);
        setTimeline(history.items);
      })
      .catch((err) => {
        if (!isMounted) return;
        setLoadError(
          err instanceof ApiError && err.status === 404
            ? "This application doesn't exist or isn't yours."
            : "Unable to load this application. Please try again."
        );
      })
      .finally(() => isMounted && setIsLoading(false));
    return () => {
      isMounted = false;
    };
  }, [applicationId]);

  /** After a successful change: take the server's tracking, and read the timeline again. */
  const applyResponse = useCallback(
    async (response: StatusChangeResponse) => {
      setApplication((current) =>
        current ? { ...current, status: response.tracking.status, tracking: response.tracking } : current
      );
      await loadTimeline();
    },
    [loadTimeline]
  );

  if (isLoading) {
    return (
      <p role="status" className="text-sm text-slate-500">
        Loading application...
      </p>
    );
  }
  if (loadError || !application) {
    return (
      <div className="flex flex-col items-start gap-3">
        <Alert variant="error">{loadError ?? "Unable to load this application."}</Alert>
        <Link href="/applications" className="text-sm font-medium text-brand-700 hover:underline">
          ← My Applications
        </Link>
      </div>
    );
  }

  const { job, source } = application.context;
  const originalUrl = safeExternalUrl(source.original_url);
  const tracking = application.tracking;
  const submission = submissionText(tracking.submission);

  return (
    <div className="flex flex-col gap-6">
      <Link href="/applications" className="text-sm font-medium text-brand-700 hover:underline">
        ← My Applications
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-500">Application tracking</p>
          <h1 className="text-2xl font-semibold text-slate-900">{job.title}</h1>
          <p className="mt-1 text-slate-600">
            {job.company_name}
            {job.location ? ` · ${job.location}` : ""} · {sourceLabel(source.source)}
          </p>
          <div className="mt-2 flex flex-wrap gap-4 text-sm font-medium">
            {originalUrl && (
              <a
                href={originalUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand-700 hover:underline"
              >
                Open original job posting ↗
              </a>
            )}
            <Link href={`/applications/${application.id}`} className="text-brand-700 hover:underline">
              Open application workspace
            </Link>
          </div>
        </div>
        <StatusBadge status={tracking.status} aria-label="Application status" className="text-sm" />
      </header>

      <Card aria-labelledby="tracking-summary-heading">
        <h2 id="tracking-summary-heading" className="text-lg font-semibold text-slate-900">
          Summary
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          {STATUS_PRESENTATION[tracking.status].description}
        </p>
        <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          <SummaryItem label="Application date" value={dateText(tracking.applied_at, "Not applied yet")} />
          <SummaryItem label="Last updated" value={dateText(tracking.last_status_change_at)} />
          {tracking.follow_up_at && (
            <SummaryItem label="Follow up on" value={dateText(tracking.follow_up_at)} />
          )}
          {submission && <SummaryItem label="Submission" value={submission} />}
        </dl>
        {tracking.submission?.needs_verification && (
          <div className="mt-3">
            <Alert variant="info">
              We couldn&apos;t tell whether the submission went through. Check with the employer,
              then record what you found in the{" "}
              <Link href={`/applications/${application.id}#browser-assistant`} className="underline">
                application workspace
              </Link>{" "}
              or set the status yourself below.
            </Alert>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card aria-labelledby="timeline-heading">
            <h2 id="timeline-heading" className="text-lg font-semibold text-slate-900">
              Timeline
            </h2>
            <div className="mt-4">
              {timelineError && <Alert variant="error">{timelineError}</Alert>}
              {timeline && <ApplicationTimeline entries={timeline} />}
              {!timeline && !timelineError && <p className="text-sm text-slate-500">Loading...</p>}
            </div>
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          <StatusChangeCard applicationId={application.id} tracking={tracking} onDone={applyResponse} />
          <FollowUpCard applicationId={application.id} tracking={tracking} onDone={applyResponse} />
          <NoteCard applicationId={application.id} onDone={applyResponse} />
          <CorrectionCard applicationId={application.id} tracking={tracking} onDone={applyResponse} />
        </div>
      </div>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-slate-900">{value}</dd>
    </div>
  );
}

// --- Actions -------------------------------------------------------------------------------------

interface ActionProps {
  applicationId: string;
  tracking: Tracking;
  onDone: (response: StatusChangeResponse) => Promise<void>;
}

/** Runs one tracking request, with its own pending / error / success state. */
function useAction(onDone: (response: StatusChangeResponse) => Promise<void>) {
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function run(
    request: () => Promise<StatusChangeResponse>,
    done: string,
    unchanged = "Nothing changed."
  ): Promise<boolean> {
    setIsSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await request();
      setMessage(response.changed ? done : unchanged);
      await onDone(response);
      return true;
    } catch (err) {
      setError(trackingErrorMessage(err, "Unable to save. Please try again."));
      return false;
    } finally {
      setIsSaving(false);
    }
  }
  return { isSaving, error, message, run };
}

function ActionFeedback({ error, message }: { error: string | null; message: string | null }) {
  return (
    <>
      {error && <Alert variant="error">{error}</Alert>}
      {message && <Alert variant="success">{message}</Alert>}
    </>
  );
}

function NoteField({
  id,
  label,
  value,
  onChange,
  required = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium text-slate-700">
        {label}
      </label>
      <textarea
        id={id}
        rows={3}
        maxLength={MAX_NOTE_LENGTH}
        className={TEXTAREA_CLASS}
        value={value}
        required={required}
        onChange={(event) => onChange(event.target.value)}
      />
      <p className="text-xs text-slate-400">Your own words: saved exactly as you write them.</p>
    </div>
  );
}

function StatusChangeCard({ applicationId, tracking, onDone }: ActionProps) {
  const [status, setStatus] = useState<ApplicationStatus | "">("");
  const [note, setNote] = useState("");
  const [followUp, setFollowUp] = useState("");
  const { isSaving, error, message, run } = useAction(onDone);
  const next = tracking.allowed_next_statuses;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!status) return;
    const body: StatusChangeRequest = { status };
    if (note.trim()) body.note = note;
    const followUpAt = acceptsFollowUp(status) ? fromDateTimeLocal(followUp) : null;
    if (followUpAt) body.follow_up_at = followUpAt;
    const ok = await run(() => changeStatus(applicationId, body), "Status updated.");
    if (ok) {
      setStatus("");
      setNote("");
      setFollowUp("");
    }
  }

  return (
    <Card aria-labelledby="change-status-heading">
      <h2 id="change-status-heading" className="text-lg font-semibold text-slate-900">
        Change status
      </h2>
      {next.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">
          This application is closed. If its status was recorded wrongly, you can correct it below.
        </p>
      ) : (
        <form onSubmit={submit} aria-label="Change status" className="mt-3 flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="next-status" className="text-sm font-medium text-slate-700">
              New status
            </label>
            <select
              id="next-status"
              className={SELECT_CLASS}
              value={status}
              onChange={(event) => setStatus(event.target.value as ApplicationStatus | "")}
            >
              <option value="">Choose a status</option>
              {next.map((option) => (
                <option key={option} value={option}>
                  {statusLabel(option)}
                </option>
              ))}
            </select>
          </div>
          {acceptsFollowUp(status) && (
            <div className="flex flex-col gap-1">
              <label htmlFor="status-follow-up" className="text-sm font-medium text-slate-700">
                Follow up on (optional)
              </label>
              <input
                id="status-follow-up"
                type="datetime-local"
                className={SELECT_CLASS}
                value={followUp}
                onChange={(event) => setFollowUp(event.target.value)}
              />
            </div>
          )}
          <NoteField id="status-note" label="Note (optional)" value={note} onChange={setNote} />
          <ActionFeedback error={error} message={message} />
          <Button type="submit" isLoading={isSaving} disabled={!status}>
            Change status
          </Button>
        </form>
      )}
    </Card>
  );
}

function FollowUpCard({ applicationId, tracking, onDone }: ActionProps) {
  const [value, setValue] = useState(toDateTimeLocal(tracking.follow_up_at));
  const { isSaving, error, message, run } = useAction(onDone);
  const canSet = acceptsFollowUp(tracking.status);

  useEffect(() => {
    setValue(toDateTimeLocal(tracking.follow_up_at));
  }, [tracking.follow_up_at]);

  async function save(event: FormEvent) {
    event.preventDefault();
    const followUpAt = fromDateTimeLocal(value);
    if (!followUpAt) return;
    await run(
      () => updateTracking(applicationId, { follow_up_at: followUpAt }),
      "Follow-up date saved."
    );
  }

  async function clear() {
    const body: TrackingUpdateRequest = { follow_up_at: null };
    const cleared = await run(() => updateTracking(applicationId, body), "Follow-up date cleared.");
    if (cleared) setValue("");
  }

  return (
    <Card aria-labelledby="follow-up-heading">
      <h2 id="follow-up-heading" className="text-lg font-semibold text-slate-900">
        Follow-up date
      </h2>
      {!canSet ? (
        <p className="mt-2 text-sm text-slate-500">
          You can set a follow-up date while the application is applied, in follow-up or at
          interview.
        </p>
      ) : (
        <form onSubmit={save} aria-label="Follow-up date" className="mt-3 flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="follow-up-at" className="text-sm font-medium text-slate-700">
              Follow up on
            </label>
            <input
              id="follow-up-at"
              type="datetime-local"
              className={SELECT_CLASS}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
            <p className="text-xs text-slate-400">
              A reminder for you. JobPilot sends nothing and does nothing when this date passes.
            </p>
          </div>
          <ActionFeedback error={error} message={message} />
          <div className="flex gap-2">
            <Button type="submit" isLoading={isSaving} disabled={!value}>
              Save date
            </Button>
            {tracking.follow_up_at && (
              <Button type="button" variant="secondary" onClick={() => void clear()} disabled={isSaving}>
                Clear date
              </Button>
            )}
          </div>
        </form>
      )}
    </Card>
  );
}

function NoteCard({
  applicationId,
  onDone,
}: {
  applicationId: string;
  onDone: ActionProps["onDone"];
}) {
  const [note, setNote] = useState("");
  const { isSaving, error, message, run } = useAction(onDone);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!note.trim()) return;
    const ok = await run(() => updateTracking(applicationId, { note }), "Note added.");
    if (ok) setNote("");
  }

  return (
    <Card aria-labelledby="add-note-heading">
      <h2 id="add-note-heading" className="text-lg font-semibold text-slate-900">
        Add a note
      </h2>
      <form onSubmit={submit} aria-label="Add a note" className="mt-3 flex flex-col gap-3">
        <NoteField id="tracking-note" label="Note" value={note} onChange={setNote} />
        <ActionFeedback error={error} message={message} />
        <Button type="submit" isLoading={isSaving} disabled={!note.trim()}>
          Add note
        </Button>
      </form>
    </Card>
  );
}

function CorrectionCard({ applicationId, tracking, onDone }: ActionProps) {
  const [status, setStatus] = useState<ApplicationStatus | "">("");
  const [note, setNote] = useState("");
  const { isSaving, error, message, run } = useAction(onDone);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!status || !note.trim()) return;
    const ok = await run(
      () => changeStatus(applicationId, { status, note, correction: true }),
      "Correction recorded."
    );
    if (ok) {
      setStatus("");
      setNote("");
    }
  }

  return (
    <Card aria-labelledby="correction-heading">
      <details>
        <summary id="correction-heading" className="cursor-pointer text-lg font-semibold text-slate-900">
          Correct the status
        </summary>
        <p className="mt-2 text-sm text-slate-500">
          Only if the status was recorded wrongly. A correction can go to any status; it is kept in
          the timeline as a correction, and needs a note saying why.
        </p>
        <form onSubmit={submit} aria-label="Correct the status" className="mt-3 flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="correction-status" className="text-sm font-medium text-slate-700">
              Correct status to
            </label>
            <select
              id="correction-status"
              className={SELECT_CLASS}
              value={status}
              onChange={(event) => setStatus(event.target.value as ApplicationStatus | "")}
            >
              <option value="">Choose a status</option>
              {ALL_STATUSES.filter((option) => option !== tracking.status).map((option) => (
                <option key={option} value={option}>
                  {statusLabel(option)}
                </option>
              ))}
            </select>
          </div>
          <NoteField
            id="correction-note"
            label="Why (required)"
            value={note}
            onChange={setNote}
            required
          />
          <ActionFeedback error={error} message={message} />
          <Button type="submit" variant="secondary" isLoading={isSaving} disabled={!status || !note.trim()}>
            Record correction
          </Button>
        </form>
      </details>
    </Card>
  );
}
