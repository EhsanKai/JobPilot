"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { verifySubmission } from "@/lib/api/submission";
import { apiMessage } from "@/lib/automation";
import { RESULT_TITLES, formatDateTime } from "@/lib/submission";
import type { SubmissionAttempt } from "@/types/submission";

interface Props {
  applicationId: string;
  attempt: SubmissionAttempt;
  onChanged?: (attempt: SubmissionAttempt) => void;
}

/**
 * What came of a submission attempt (V0.11). "Submitted successfully" only when the
 * employer's page confirmed it -- never because a button was pressed. An unknown result
 * says so plainly, promises no automatic retry, and lets the user record what they found.
 */
export function SubmissionResult({ applicationId, attempt, onChanged }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const result = attempt.result ?? "IN_PROGRESS";
  const verified = attempt.user_verified_outcome;

  async function record(outcome: "SUBMITTED" | "NOT_SUBMITTED") {
    setBusy(outcome);
    setError(null);
    try {
      onChanged?.(await verifySubmission(applicationId, attempt.id, outcome));
    } catch (e) {
      setError(apiMessage(e, "Unable to record what you found."));
    } finally {
      setBusy(null);
    }
  }

  const details = (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      {attempt.company && (
        <>
          <dt className="text-slate-500">Company</dt>
          <dd>{attempt.company}</dd>
        </>
      )}
      {attempt.position && (
        <>
          <dt className="text-slate-500">Position</dt>
          <dd>{attempt.position}</dd>
        </>
      )}
      <dt className="text-slate-500">Website</dt>
      <dd>{attempt.destination_domain}</dd>
      {attempt.submit_triggered_at && (
        <>
          <dt className="text-slate-500">{result === "SUCCESS" ? "Submitted" : "Final button pressed"}</dt>
          <dd>{formatDateTime(attempt.submit_triggered_at)}</dd>
        </>
      )}
      {attempt.confirmation_reference && (
        <>
          <dt className="text-slate-500">Confirmation</dt>
          <dd>Application reference {attempt.confirmation_reference}</dd>
        </>
      )}
    </dl>
  );

  return (
    <section aria-label="Submission result" data-testid="submission-result" className="flex flex-col gap-2">
      {result === "SUCCESS" && (
        <Alert variant="success">
          <strong>{RESULT_TITLES.SUCCESS}</strong>
          {details}
        </Alert>
      )}
      {result === "FAILED_BEFORE_SUBMIT" && (
        <Alert variant="error">
          <strong>{RESULT_TITLES.FAILED_BEFORE_SUBMIT}</strong> No submission was confirmed — the
          final button was not pressed.
          {attempt.failure_reason && <p className="mt-1">{attempt.failure_reason}</p>}
        </Alert>
      )}
      {result === "SUBMISSION_UNKNOWN" && (
        <Alert variant="error">
          <strong>{RESULT_TITLES.SUBMISSION_UNKNOWN}</strong>
          <p className="mt-1">
            For your safety, JobPilot will NOT submit again automatically. Please verify the
            application manually (check your email or the employer&apos;s website).
          </p>
          {attempt.failure_reason && <p className="mt-1">{attempt.failure_reason}</p>}
          {details}
        </Alert>
      )}
      {result === "IN_PROGRESS" && <Alert variant="info">{RESULT_TITLES.IN_PROGRESS}</Alert>}

      {result === "SUBMISSION_UNKNOWN" && !verified && (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="What did you find?">
          <span className="text-sm text-slate-600">After checking, I found that it was:</span>
          <Button
            variant="secondary"
            isLoading={busy === "SUBMITTED"}
            onClick={() => void record("SUBMITTED")}
          >
            Received by the employer
          </Button>
          <Button
            variant="secondary"
            isLoading={busy === "NOT_SUBMITTED"}
            onClick={() => void record("NOT_SUBMITTED")}
          >
            Not received
          </Button>
        </div>
      )}
      {verified && (
        <p className="text-sm text-slate-600">
          You recorded:{" "}
          {verified === "SUBMITTED"
            ? "the employer received the application."
            : "the application was not received. You can review it again."}
        </p>
      )}
      {error && <Alert variant="error">{error}</Alert>}
    </section>
  );
}
