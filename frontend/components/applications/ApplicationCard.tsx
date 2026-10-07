"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { startApplication } from "@/lib/api/applications";
import { APPLICATION_STATUS_LABELS } from "@/lib/applications";
import { ApiError } from "@/lib/http";
import type { ApplicationRef } from "@/types/application";

/**
 * "Start Application" / "Continue Application" on the job details page.
 *
 * Starting only creates (or reopens) the user's private preparation
 * workspace for the job: it never submits anything. It happens only when the
 * user clicks, never because a job was opened, saved or matched.
 */
export function ApplicationCard({
  jobId,
  companyName,
  application,
}: {
  jobId: string;
  companyName: string;
  application: ApplicationRef | null | undefined;
}) {
  const router = useRouter();
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setIsStarting(true);
    setError(null);
    try {
      // Starting twice (a double click, another tab) returns the same
      // application, so this can't create a duplicate.
      const started = await startApplication(jobId);
      router.push(`/applications/${started.id}`);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 404
          ? "This job is no longer in your job list."
          : err instanceof ApiError
            ? err.message
            : "Unable to start the application. Please try again."
      );
      setIsStarting(false);
    }
  }

  return (
    <Card aria-labelledby="application-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="application-heading" className="text-lg font-semibold text-slate-900">
            Application
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {application
              ? "Your private workspace for this job is ready."
              : `Prepare your application in a private workspace: salary expectation, availability, notice period, language and notes. Nothing is submitted or sent to ${companyName}.`}
          </p>
        </div>
        {application ? (
          <div className="flex flex-wrap items-center gap-3">
            <span
              className="rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700"
              aria-label="Application status"
            >
              {APPLICATION_STATUS_LABELS[application.status]}
            </span>
            <Link
              href={`/applications/${application.id}`}
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
            >
              Continue Application
            </Link>
          </div>
        ) : (
          <Button onClick={() => void start()} isLoading={isStarting}>
            Start Application
          </Button>
        )}
      </div>
      {error && (
        <div className="mt-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}
    </Card>
  );
}
