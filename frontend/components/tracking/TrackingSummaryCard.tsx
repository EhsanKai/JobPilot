import Link from "next/link";

import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/tracking/StatusBadge";
import { dateText, submissionText } from "@/lib/tracking";
import type { ApplicationTracking } from "@/types/application";

/**
 * Tracking at a glance, in the Application Workspace: the current status, when the
 * application was submitted, the follow-up date and the latest submission result -- with a link
 * to the full timeline. It only reads; changing the status happens on the tracking page.
 */
export function TrackingSummaryCard({
  applicationId,
  tracking,
}: {
  applicationId: string;
  tracking: ApplicationTracking;
}) {
  const submission = submissionText(tracking.submission);
  return (
    <Card aria-labelledby="tracking-heading">
      <h2 id="tracking-heading" className="text-lg font-semibold text-slate-900">
        Tracking
      </h2>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        <dt className="text-slate-500">Current status</dt>
        <dd>
          <StatusBadge status={tracking.status} aria-label="Tracking status" />
        </dd>
        <dt className="text-slate-500">Applied</dt>
        <dd className="text-slate-900">{dateText(tracking.applied_at, "Not applied yet")}</dd>
        <dt className="text-slate-500">Last change</dt>
        <dd className="text-slate-900">{dateText(tracking.last_status_change_at)}</dd>
        {tracking.follow_up_at && (
          <>
            <dt className="text-slate-500">Follow up on</dt>
            <dd className="text-slate-900">{dateText(tracking.follow_up_at)}</dd>
          </>
        )}
        {submission && (
          <>
            <dt className="text-slate-500">Submission</dt>
            <dd className="text-slate-900">{submission}</dd>
          </>
        )}
      </dl>
      <Link
        href={`/applications/${applicationId}/tracking`}
        className="mt-4 inline-block text-sm font-medium text-brand-700 hover:underline"
      >
        Timeline &amp; status →
      </Link>
    </Card>
  );
}
