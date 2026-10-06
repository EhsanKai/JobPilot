"use client";

import Link from "next/link";

import { Button } from "@/components/ui/Button";
import {
  EMPLOYMENT_TYPE_LABELS,
  WORK_MODEL_LABELS,
  formatDate,
  formatSalary,
  safeExternalUrl,
  sourceIndicator,
  sourceLabel,
} from "@/lib/jobs";
import type { JobSummary } from "@/types/jobs";

interface JobCardProps {
  job: JobSummary;
  isSaving?: boolean;
  onToggleSave: (job: JobSummary) => void;
}

export function JobCard({ job, isSaving = false, onToggleSave }: JobCardProps) {
  const salary = formatSalary(job);
  const posted = formatDate(job.posted_at);
  const originalUrl = safeExternalUrl(job.canonical_url);
  const facts = [
    job.location,
    job.work_model ? WORK_MODEL_LABELS[job.work_model] : null,
    job.employment_type ? EMPLOYMENT_TYPE_LABELS[job.employment_type] : null,
    salary,
  ].filter(Boolean);

  return (
    <article
      className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      aria-labelledby={`job-${job.id}-title`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={`job-${job.id}-title`} className="text-base font-semibold text-slate-900">
            <Link href={`/jobs/${job.id}`} className="hover:text-brand-700 hover:underline">
              {job.title}
            </Link>
          </h3>
          <p className="text-sm text-slate-600">{job.company_name}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!job.is_active && (
            <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800">
              No longer listed
            </span>
          )}
          <span
            className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600"
            title={job.sources.length > 1 ? job.sources.map(sourceLabel).join(", ") : undefined}
          >
            {sourceIndicator(job)}
          </span>
        </div>
      </div>

      {facts.length > 0 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-700" aria-label="Job facts">
          {facts.map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          {posted ? `Posted ${posted}` : "Posting date not stated"}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => onToggleSave(job)}
            isLoading={isSaving}
            aria-pressed={job.saved}
          >
            {job.saved ? "Saved ✓" : "Save"}
          </Button>
          <Link
            href={`/jobs/${job.id}`}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
          >
            Details
          </Link>
          {originalUrl && (
            <a
              href={originalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
            >
              View Job ↗
            </a>
          )}
        </div>
      </div>
    </article>
  );
}
