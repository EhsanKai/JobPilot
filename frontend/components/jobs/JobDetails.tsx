"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { MatchPanel } from "@/components/jobs/MatchPanel";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { getJob, saveJob, unsaveJob } from "@/lib/api/jobs";
import { ApiError } from "@/lib/http";
import {
  EMPLOYMENT_TYPE_LABELS,
  POSTING_STATUS_LABELS,
  SENIORITY_LABELS,
  WORK_MODEL_LABELS,
  formatDate,
  formatSalary,
  safeExternalUrl,
  sourceLabel,
} from "@/lib/jobs";
import type { JobDetail, JobSourcePosting } from "@/types/jobs";

const NOT_STATED = "Not stated";

export function JobDetails({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    getJob(jobId)
      .then((result) => isMounted && setJob(result))
      .catch((err) => {
        if (!isMounted) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? "This job doesn't exist or isn't in your job list."
            : "Unable to load this job. Please try again."
        );
      })
      .finally(() => isMounted && setIsLoading(false));
    return () => {
      isMounted = false;
    };
  }, [jobId]);

  async function toggleSave() {
    if (!job) return;
    setIsSaving(true);
    try {
      setJob(job.saved ? await unsaveJob(job.id) : await saveJob(job.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update the saved job.");
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) return <p className="text-sm text-slate-500">Loading job...</p>;
  if (!job) {
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="error">{error ?? "Job not found."}</Alert>
        <Link href="/jobs" className="text-sm font-medium text-brand-700 hover:underline">
          ← Back to jobs
        </Link>
      </div>
    );
  }

  const originalUrl = safeExternalUrl(job.canonical_url);
  const applyUrl = safeExternalUrl(job.apply_url);
  const salary = formatSalary(job) ?? job.salary_text;
  const postingCount = job.source_postings.length;
  const preferred = job.source_postings.find((p) => p.is_preferred) ?? null;

  return (
    <div className="flex flex-col gap-6">
      <Link href="/jobs" className="text-sm font-medium text-brand-700 hover:underline">
        ← Back to jobs
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">{job.title}</h1>
          <p className="mt-1 text-slate-600">
            {job.company_name}
            {job.location ? ` · ${job.location}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={toggleSave} isLoading={isSaving} aria-pressed={job.saved}>
            {job.saved ? "Saved ✓" : "Save"}
          </Button>
          {originalUrl && (
            <a
              href={originalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
            >
              View original posting ↗
            </a>
          )}
        </div>
      </div>

      {error && <Alert variant="error">{error}</Alert>}

      <MatchPanel jobId={job.id} />

      <Card>
        <h2 className="text-lg font-semibold text-slate-900">JobPilot data</h2>
        <p className="mt-1 text-sm text-slate-500">
          Normalized by JobPilot from{" "}
          {postingCount > 1 ? `${postingCount} source postings of this job` : "the posting"}, without
          AI. &quot;{NOT_STATED}&quot; means the posting doesn&apos;t say.
        </p>
        <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <Fact label="Salary" value={salary} />
          <Fact label="Work model" value={job.work_model ? WORK_MODEL_LABELS[job.work_model] : null} />
          <Fact
            label="Employment type"
            value={job.employment_type ? EMPLOYMENT_TYPE_LABELS[job.employment_type] : null}
          />
          <Fact label="Seniority" value={job.seniority ? SENIORITY_LABELS[job.seniority] : null} />
          <Fact label="Posted" value={formatDate(job.posted_at)} />
          <Fact
            label="Locations"
            value={job.locations.length > 0 ? job.locations.join(" · ") : job.location}
          />
          <Fact label="Department" value={job.department} />
          <Fact
            label={postingCount > 1 ? "Preferred source" : "Source"}
            value={`${sourceLabel(job.source)}${job.source_company_key ? ` (${job.source_company_key})` : ""}`}
          />
          {postingCount > 1 && (
            <Fact label="Found on" value={job.sources.map(sourceLabel).join(" · ")} />
          )}
          <Fact label="First found for you" value={formatDate(job.first_discovered_by_you_at)} />
          <Fact label="Last seen at source" value={formatDate(job.last_seen_at)} />
        </dl>
        {job.technologies.length > 0 && (
          <div className="mt-4">
            <p className="text-sm font-medium text-slate-500">Technologies mentioned</p>
            <ul className="mt-2 flex flex-wrap gap-2" aria-label="Technologies">
              {job.technologies.map((tech) => (
                <li key={tech} className="rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700">
                  {tech}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      <SourcesCard postings={job.source_postings} />

      <Card>
        <h2 className="text-lg font-semibold text-slate-900">Original job posting</h2>
        <p className="mt-1 text-sm text-slate-500">
          Text from {job.company_name}&apos;s posting on {sourceLabel(job.source)}
          {postingCount > 1 && preferred ? " (the preferred source)" : ""}. The original
          posting is the authoritative version
          {originalUrl ? (
            <>
              {": "}
              <a
                href={originalUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="break-all text-brand-700 underline"
              >
                {originalUrl}
              </a>
            </>
          ) : (
            "."
          )}
        </p>

        {job.responsibilities.length > 0 && (
          <PostingList title="Responsibilities" items={job.responsibilities} />
        )}
        {job.requirements.length > 0 && <PostingList title="Requirements" items={job.requirements} />}

        <h3 className="mt-6 text-sm font-semibold text-slate-900">Full description</h3>
        {job.description ? (
          <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-slate-700">
            {job.description}
          </p>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            The source didn&apos;t provide a description. Open the original posting to read it.
          </p>
        )}

        {applyUrl && (
          <p className="mt-6 text-sm">
            <a
              href={applyUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-brand-700 underline"
            >
              Apply on the company&apos;s site ↗
            </a>
          </p>
        )}
      </Card>
    </div>
  );
}

/** Every source posting of the job, with its original link. */
function SourcesCard({ postings }: { postings: JobSourcePosting[] }) {
  if (postings.length === 0) return null;
  return (
    <Card>
      <h2 className="text-lg font-semibold text-slate-900">Sources</h2>
      <p className="mt-1 text-sm text-slate-500">
        Found on {postings.length === 1 ? "one source" : `${postings.length} sources`}. Each
        entry is the original posting, as that source lists it.
      </p>
      <ul className="mt-4 flex flex-col divide-y divide-slate-100" aria-label="Found on">
        {postings.map((posting) => (
          <SourcePostingItem key={posting.id} posting={posting} />
        ))}
      </ul>
    </Card>
  );
}

function SourcePostingItem({ posting }: { posting: JobSourcePosting }) {
  const label = sourceLabel(posting.source);
  const url = safeExternalUrl(posting.original_url);
  const statusClass =
    posting.status === "active"
      ? "bg-emerald-50 text-emerald-700"
      : posting.status === "stale"
        ? "bg-slate-100 text-slate-600"
        : "bg-amber-50 text-amber-800";
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3" aria-label={label}>
      <div className="flex flex-col gap-1 text-sm">
        <p className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-900">{label}</span>
          {posting.source_company_key && (
            <span className="text-slate-500">({posting.source_company_key})</span>
          )}
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusClass}`}>
            {POSTING_STATUS_LABELS[posting.status]}
          </span>
          {posting.is_preferred && (
            <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700">
              Preferred
            </span>
          )}
        </p>
        <p className="text-slate-700">
          Listed as &quot;{posting.title}&quot;
          {posting.location ? ` in ${posting.location}` : ""}
        </p>
        <p className="text-xs text-slate-500">
          First seen {formatDate(posting.first_seen_at) ?? "—"} · Last seen{" "}
          {formatDate(posting.last_seen_at) ?? "—"}
        </p>
      </div>
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`View original posting on ${label}`}
          className="text-sm font-medium text-brand-700 underline"
        >
          View original posting →
        </a>
      )}
    </li>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className={value ? "text-slate-900" : "text-slate-400"}>{value ?? NOT_STATED}</dd>
    </div>
  );
}

function PostingList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="mt-6">
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
