"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import {
  authorizeConsent,
  authorizeSubmission,
  confirmFinalReview,
  getFinalReview,
  revokeConsent,
  submitApplication,
} from "@/lib/api/submission";
import { apiMessage } from "@/lib/automation";
import {
  VERIFICATION_LABELS,
  documentLabel,
  fieldValue,
  formatDateTime,
  originLabel,
  sourceLabel,
} from "@/lib/submission";
import type { AutomationSession } from "@/types/automation";
import type {
  FinalReviewResponse,
  ReviewConsent,
  ReviewField,
  SubmissionAttempt,
} from "@/types/submission";

interface Props {
  applicationId: string;
  session: AutomationSession;
  /** The user takes the browser over (from the review). */
  onTakeControl: () => void;
  /** An attempt finished (whatever its result). */
  onAttempt: (attempt: SubmissionAttempt) => void;
  /** The submission request is running (it outlives this view: the press ends the session). */
  onSubmitting?: (running: boolean) => void;
}

const REASONS: Record<string, string> = {
  USER_TAKEOVER: "you took control of the browser",
  PAUSED: "the assistant was paused",
  STOPPED: "the assistant was stopped",
  RESUMED: "the assistant was resumed",
  FILL: "the form was filled again",
  MAPPING_CHANGED: "a field was matched to another question",
  DOCUMENT_CHANGED: "a document choice changed",
  CONSENT_CHANGED: "a consent changed",
  NAVIGATION: "the page was reloaded or changed",
  DOMAIN_CHANGED: "the browser moved to another website",
  STATE_CHANGED: "something that would be submitted changed",
  REVIEW_SUPERSEDED: "you reviewed it again",
  USER_CANCELLED: "you withdrew your authorization",
  BROWSER_GONE: "the browser closed",
};

function reasonText(code: string | null): string {
  return code ? (REASONS[code] ?? code.toLowerCase().replace(/_/g, " ")) : "something changed";
}

/**
 * The final stage of an application (V0.11):
 *
 *     Final review -> Consents -> Authorize submission -> Submitting... -> Result
 *
 * It shows exactly what would be submitted -- read from the live page -- where it would go,
 * where every value came from, which document versions and which consents. Nothing is
 * submitted from here except through the deliberate last step: ticking "I have reviewed ...
 * and authorize JobPilot to submit" and pressing "Authorize & Submit". The server checks all
 * of it again; this view only asks.
 */
export function FinalReview({
  applicationId,
  session,
  onTakeControl,
  onAttempt,
  onSubmitting,
}: Props) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<FinalReviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [agreed, setAgreed] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const loaded = await getFinalReview(applicationId, session.id);
      if (!mounted.current) return;
      setData(loaded);
      if (!loaded.review_is_current) setAgreed(false);
    } catch (e) {
      if (mounted.current) setError(apiMessage(e, "Unable to load the final review."));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [applicationId, session.id]);

  // Anything that happens in the assistant may change what would be submitted: look again.
  const updated = session.updated_at;
  useEffect(() => {
    if (open) void load();
  }, [open, updated, load]);

  async function act(name: string, action: () => Promise<unknown>, fallback: string) {
    setBusy(name);
    setError(null);
    try {
      await action();
    } catch (e) {
      if (mounted.current) setError(apiMessage(e, fallback));
    } finally {
      if (mounted.current) setBusy(null);
      await load();
    }
  }

  async function authorizeAndSubmit() {
    if (!data?.review || !agreed) return;
    setBusy("submit");
    setError(null);
    onSubmitting?.(true);
    try {
      const authorization = await authorizeSubmission(
        applicationId,
        session.id,
        data.review.id,
        data.review.state_hash
      );
      const attempt = await submitApplication(applicationId, session.id, authorization.id);
      // Always reported, even if this view is gone meanwhile: the press ends the session,
      // so the panel may hide the review while JobPilot still waits for a confirmation.
      onAttempt(attempt);
      if (mounted.current) setAgreed(false);
    } catch (e) {
      if (mounted.current) setError(apiMessage(e, "The application was not submitted."));
      if (mounted.current) await load();
    } finally {
      onSubmitting?.(false);
      if (mounted.current) setBusy(null);
    }
  }

  if (!open) {
    return (
      <section aria-label="Final review" className="flex flex-col gap-2 border-t border-slate-200 pt-4">
        <h3 className="text-base font-semibold text-slate-900">Final review</h3>
        <p className="text-sm text-slate-600">
          When the form is complete, review exactly what would be sent. JobPilot submits only
          after your final review and your explicit authorization — and then exactly once.
        </p>
        <div>
          <Button variant="secondary" onClick={() => setOpen(true)}>
            Open final review
          </Button>
        </div>
      </section>
    );
  }

  const current = data?.current;
  const personal = current?.fields.filter((f) => f.group === "personal") ?? [];
  const questions = current?.fields.filter((f) => f.group !== "personal") ?? [];
  const submitting = busy === "submit";

  return (
    <section
      aria-label="Final review"
      data-testid="final-review"
      className="flex flex-col gap-4 border-t border-slate-200 pt-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-900">Final review</h3>
        <Button variant="secondary" isLoading={loading && !!data} onClick={() => void load()}>
          Check again
        </Button>
      </div>
      {error && <Alert variant="error">{error}</Alert>}
      {!data && loading && <p className="text-sm text-slate-500">Reading the form...</p>}

      {current && (
        <>
          {data.review_is_current && data.review && (
            <Alert variant="success">
              You confirmed this review at {formatDateTime(data.review.reviewed_at)}. It is still
              exactly what would be submitted.
            </Alert>
          )}
          {!data.review_is_current && data.review && data.review.status !== "USED" && (
            <Alert variant="info">
              Your earlier review is no longer current ({reasonText(data.review.invalidation_reason)}).
              Look at it again and confirm it.
            </Alert>
          )}

          <div>
            <h4 className="text-sm font-semibold text-slate-900">1. Destination</h4>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              {current.destination.company && (
                <>
                  <dt className="text-slate-500">Company</dt>
                  <dd>{current.destination.company}</dd>
                </>
              )}
              {current.destination.position && (
                <>
                  <dt className="text-slate-500">Role</dt>
                  <dd>{current.destination.position}</dd>
                </>
              )}
              <dt className="text-slate-500">Website</dt>
              <dd data-testid="destination-host">{current.destination.host || "—"}</dd>
              <dt className="text-slate-500">Page</dt>
              <dd className="break-all">{current.destination.url || "—"}</dd>
              {current.destination.source && (
                <>
                  <dt className="text-slate-500">Found via</dt>
                  <dd>{current.destination.source}</dd>
                </>
              )}
              {current.submit_control && (
                <>
                  <dt className="text-slate-500">Final button</dt>
                  <dd>&ldquo;{current.submit_control.label}&rdquo;</dd>
                </>
              )}
            </dl>
          </div>

          <ValueTable title="2. Personal information" fields={personal} />
          <ValueTable title="3. Application questions" fields={questions} showQuestion />

          <div>
            <h4 className="text-sm font-semibold text-slate-900">4. Documents</h4>
            {current.documents.length === 0 ? (
              <p className="text-sm text-slate-500">The form asks for no files.</p>
            ) : (
              <ul className="mt-1 flex flex-col gap-1 text-sm" aria-label="Documents">
                {current.documents.map((doc) => (
                  <li key={doc.field_key}>
                    <span className="text-slate-500">{doc.label}: </span>
                    <strong>{documentLabel(doc)}</strong>
                    {doc.origin && <span className="text-slate-500"> ({originLabel(doc.origin)})</span>}
                    {doc.verification === "VERIFIED" && (
                      <span className="text-slate-500"> — attached and checked</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div id="final-review-consents">
            <h4 className="text-sm font-semibold text-slate-900">5. Consents</h4>
            {current.consents.length === 0 ? (
              <p className="text-sm text-slate-500">The form asks for no consent.</p>
            ) : (
              <ul className="mt-1 flex flex-col gap-3" aria-label="Consents">
                {current.consents.map((consent, index) => (
                  <ConsentItem
                    key={consent.field_key}
                    index={index + 1}
                    consent={consent}
                    busy={busy}
                    onGive={() =>
                      act(
                        `consent-${consent.field_key}`,
                        () =>
                          authorizeConsent(
                            applicationId,
                            session.id,
                            consent.field_id ?? "",
                            consent.text_hash
                          ),
                        "Unable to record your consent."
                      )
                    }
                    onWithdraw={() =>
                      act(
                        `consent-${consent.field_key}`,
                        () => revokeConsent(applicationId, session.id, consent.field_id ?? ""),
                        "Unable to withdraw your consent."
                      )
                    }
                  />
                ))}
              </ul>
            )}
          </div>

          <div>
            <h4 className="text-sm font-semibold text-slate-900">6. Warnings</h4>
            {current.blockers.length === 0 && current.warnings.length === 0 && (
              <p className="text-sm text-slate-600">None.</p>
            )}
            {current.blockers.length > 0 && (
              <div role="alert" className="mt-1 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                <p className="font-medium">Must be resolved before anything can be submitted:</p>
                <ul className="mt-1 list-disc pl-5" aria-label="Blockers">
                  {current.blockers.map((b) => (
                    <li key={`${b.code}-${b.field_id ?? ""}`} data-code={b.code}>
                      {b.label ? <strong>{b.label}: </strong> : null}
                      {b.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {current.warnings.length > 0 && (
              <ul className="mt-1 list-disc pl-5 text-sm text-slate-600" aria-label="Notes">
                {current.warnings.map((w) => (
                  <li key={`${w.code}-${w.field_id ?? ""}`}>
                    {w.label ? <strong>{w.label}: </strong> : null}
                    {w.message}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-wrap gap-2" role="group" aria-label="Edit before submitting">
            <a className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" href="#questions">
              Edit answers
            </a>
            <a className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" href="#documents">
              Change CV or Anschreiben
            </a>
            <a className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" href="#final-review-consents">
              Review consents
            </a>
            {session.controls.can_take_control && (
              <Button variant="secondary" onClick={onTakeControl}>
                Take control
              </Button>
            )}
          </div>
          <p className="text-xs text-slate-500">
            Any change — an answer, a document, a consent, the page — makes this review out of
            date. You then review it again; nothing is re-authorized by itself.
          </p>

          {!data.review_is_current && (
            <div>
              <Button
                disabled={!data.can_confirm_review}
                isLoading={busy === "confirm"}
                onClick={() =>
                  void act(
                    "confirm",
                    () => confirmFinalReview(applicationId, session.id, current.state_hash),
                    "Unable to confirm the review."
                  )
                }
              >
                Confirm final review
              </Button>
              {!data.can_confirm_review && (
                <p className="mt-1 text-xs text-slate-500">
                  Resolve the warnings above first.
                </p>
              )}
            </div>
          )}

          {data.review_is_current && data.review && (
            <div className="flex flex-col gap-3 rounded-lg border border-slate-300 bg-slate-50 p-4">
              <h4 className="text-sm font-semibold text-slate-900">Authorize submission</h4>
              <label className="flex items-start gap-2 text-sm text-slate-900">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={agreed}
                  disabled={!data.can_authorize || submitting}
                  onChange={(e) => setAgreed(e.target.checked)}
                />
                <span>
                  I have reviewed the information above and authorize JobPilot to submit this
                  application to <strong>{data.review.destination_host}</strong>, once.
                </span>
              </label>
              <div>
                <Button
                  variant="danger"
                  disabled={!agreed || !data.can_authorize}
                  isLoading={submitting}
                  onClick={() => void authorizeAndSubmit()}
                >
                  {submitting ? "Submitting..." : "Authorize & Submit"}
                </Button>
              </div>
              {submitting && (
                <p role="status" className="text-sm text-slate-600">
                  Submitting... JobPilot presses the final button once and waits for the
                  employer&apos;s confirmation. It never presses it twice.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ValueTable({
  title,
  fields,
  showQuestion = false,
}: {
  title: string;
  fields: ReviewField[];
  showQuestion?: boolean;
}) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-slate-900">{title}</h4>
      {fields.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing in this section.</p>
      ) : (
        <div className="overflow-x-auto">
          <table aria-label={title.replace(/^\d+\. /, "")} className="mt-1 w-full min-w-[32rem] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <th scope="col" className="py-1 pr-3 font-medium">
                  {showQuestion ? "Question" : "Field"}
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Value
                </th>
                <th scope="col" className="py-1 pr-3 font-medium">
                  Source
                </th>
                <th scope="col" className="py-1 font-medium">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {fields.map((field) => (
                <tr key={field.field_key} aria-label={`Review ${field.label}`}>
                  <td className="py-1 pr-3 text-slate-700">
                    {field.label}
                    {field.required && <span className="text-slate-400"> *</span>}
                  </td>
                  <td className="whitespace-pre-wrap py-1 pr-3 font-medium text-slate-900">
                    {fieldValue(field)}
                  </td>
                  <td className="py-1 pr-3 text-slate-600">{sourceLabel(field)}</td>
                  <td className="py-1 text-slate-600">
                    {VERIFICATION_LABELS[field.verification] ?? field.verification}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ConsentItem({
  index,
  consent,
  busy,
  onGive,
  onWithdraw,
}: {
  index: number;
  consent: ReviewConsent;
  busy: string | null;
  onGive: () => void;
  onWithdraw: () => void;
}) {
  const name = `Consent ${index}`;
  const loading = busy === `consent-${consent.field_key}`;
  return (
    <li aria-label={name} className="rounded-lg border border-slate-200 p-3 text-sm">
      <p className="font-medium text-slate-900">
        {name}
        {consent.required ? " (required)" : " (optional)"}
      </p>
      <blockquote className="mt-1 border-l-2 border-slate-300 pl-3 text-slate-800">
        &ldquo;{consent.text}&rdquo;
      </blockquote>
      <p className="mt-1 text-slate-600">
        {consent.authorized ? "✓ Authorized by you" : "Not authorized by you"} ·{" "}
        {consent.given ? "given in the form" : "not given in the form"}
      </p>
      <div className="mt-2">
        {consent.authorized ? (
          <Button variant="secondary" isLoading={loading} onClick={onWithdraw}>
            Withdraw consent {index}
          </Button>
        ) : (
          <Button variant="secondary" isLoading={loading} onClick={onGive}>
            I give consent {index}
          </Button>
        )}
      </div>
    </li>
  );
}
