"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { DiscoveryStatus } from "@/components/jobs/DiscoveryStatus";
import { JobCard } from "@/components/jobs/JobCard";
import { JobSearchForm } from "@/components/jobs/JobSearchForm";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  getDiscoveryRun,
  getLatestDiscoveryRun,
  listJobSources,
  listJobs,
  saveJob,
  startDiscovery,
  unsaveJob,
} from "@/lib/api/jobs";
import { ApiError } from "@/lib/http";
import { EMPTY_SEARCH, type JobSearchValues, toCriteria, toListQuery } from "@/lib/jobSearch";
import type { DiscoveryRun, JobList, JobListQuery, JobSource, JobSummary } from "@/types/jobs";

const DEFAULT_POLL_INTERVAL_MS = 2000;
// ~5 minutes at the default interval; the backend gives up on runs well before.
const MAX_POLLS = 150;
const PAGE_SIZE = 20;

interface JobsDashboardProps {
  pollIntervalMs?: number;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function JobsDashboard({ pollIntervalMs = DEFAULT_POLL_INTERVAL_MS }: JobsDashboardProps) {
  const [values, setValues] = useState<JobSearchValues>(EMPTY_SEARCH);
  const [query, setQuery] = useState<JobListQuery>({});
  const [savedOnly, setSavedOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [jobs, setJobs] = useState<JobList | null>(null);
  const [isLoadingJobs, setIsLoadingJobs] = useState(true);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [sources, setSources] = useState<JobSource[]>([]);
  const [run, setRun] = useState<DiscoveryRun | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [savingIds, setSavingIds] = useState<Set<string>>(new Set());

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCount = useRef(0);
  const mounted = useRef(true);

  const loadJobs = useCallback(async () => {
    setIsLoadingJobs(true);
    setJobsError(null);
    try {
      const result = await listJobs({
        ...query,
        saved: savedOnly || undefined,
        sort: savedOnly ? "saved" : undefined,
        page,
        page_size: PAGE_SIZE,
      });
      if (mounted.current) setJobs(result);
    } catch (error) {
      if (mounted.current) setJobsError(errorMessage(error, "Unable to load jobs. Please try again."));
    } finally {
      if (mounted.current) setIsLoadingJobs(false);
    }
  }, [query, savedOnly, page]);

  const loadJobsRef = useRef(loadJobs);
  loadJobsRef.current = loadJobs;

  const poll = useCallback(
    (runId: string) => {
      if (timer.current) clearTimeout(timer.current);
      pollCount.current = 0;
      const tick = async () => {
        pollCount.current += 1;
        try {
          const latest = await getDiscoveryRun(runId);
          if (!mounted.current) return;
          setRun(latest);
          if (!latest.is_active) {
            void loadJobsRef.current();
            return;
          }
        } catch (error) {
          if (!mounted.current) return;
          setRunError(errorMessage(error, "Lost track of the discovery run. Please refresh."));
          return;
        }
        if (pollCount.current >= MAX_POLLS) {
          setRunError("Discovery is taking longer than expected. Please check back later.");
          return;
        }
        timer.current = setTimeout(tick, pollIntervalMs);
      };
      timer.current = setTimeout(tick, pollIntervalMs);
    },
    [pollIntervalMs]
  );

  // Initial load: sources (for the filter), and the latest run so a discovery
  // started before a page reload keeps showing its progress.
  useEffect(() => {
    mounted.current = true;
    listJobSources()
      .then((result) => mounted.current && setSources(result))
      .catch(() => {
        // Non-critical: the source filter just shows "All sources".
      });
    getLatestDiscoveryRun()
      .then((latest) => {
        if (!mounted.current || !latest) return;
        setRun(latest);
        if (latest.is_active) poll(latest.id);
      })
      .catch(() => {
        // Non-critical: no status banner.
      });
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  async function discover(useFilters: boolean) {
    setRunError(null);
    try {
      const started = await startDiscovery(useFilters ? toCriteria(values) : undefined);
      setRun(started);
      poll(started.id);
    } catch (error) {
      // A run is already in progress: follow it instead of failing.
      const activeRunId = error instanceof ApiError ? error.details?.run_id : undefined;
      if (error instanceof ApiError && error.status === 409 && typeof activeRunId === "string") {
        poll(activeRunId);
        return;
      }
      setRunError(errorMessage(error, "Unable to start job discovery. Please try again."));
    }
  }

  function applyFilters() {
    setPage(1);
    setQuery(toListQuery(values));
  }

  async function toggleSave(job: JobSummary) {
    setSavingIds((ids) => new Set(ids).add(job.id));
    try {
      const updated = job.saved ? await unsaveJob(job.id) : await saveJob(job.id);
      setJobs((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.id === updated.id
                  ? { ...item, saved: updated.saved, saved_at: updated.saved_at }
                  : item
              ),
            }
          : current
      );
    } catch (error) {
      setJobsError(errorMessage(error, "Unable to update the saved job. Please try again."));
    } finally {
      setSavingIds((ids) => {
        const next = new Set(ids);
        next.delete(job.id);
        return next;
      });
    }
  }

  const isDiscovering = Boolean(run?.is_active);
  const hasFilters = Object.values(query).some(Boolean) || savedOnly;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <JobSearchForm
          values={values}
          sources={sources}
          isDiscovering={isDiscovering}
          onChange={setValues}
          onSearch={applyFilters}
          onDiscover={() => void discover(true)}
          onDiscoverWithPreferences={() => void discover(false)}
        />
      </Card>

      <DiscoveryStatus run={run} error={runError} />

      <section aria-labelledby="job-results-heading" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="job-results-heading" className="text-lg font-semibold text-slate-900">
            {savedOnly ? "Saved jobs" : "Your jobs"}
            {jobs && !isLoadingJobs && (
              <span className="ml-2 text-sm font-normal text-slate-500">({jobs.total})</span>
            )}
          </h2>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={savedOnly}
              onChange={(e) => {
                setPage(1);
                setSavedOnly(e.target.checked);
              }}
            />
            Saved only
          </label>
        </div>

        {jobsError && <Alert variant="error">{jobsError}</Alert>}

        {isLoadingJobs && !jobs && <p className="text-sm text-slate-500">Loading jobs...</p>}

        {jobs && jobs.items.length === 0 && !isLoadingJobs && (
          <Card className="text-center">
            <p className="font-medium text-slate-900">No jobs found</p>
            <p className="mt-1 text-sm text-slate-500">
              {hasFilters
                ? "No jobs match these filters. Try fewer filters, or discover new jobs."
                : "JobPilot hasn't found any jobs for you yet. Click \"Discover jobs\" to search the job sources."}
            </p>
          </Card>
        )}

        {jobs && jobs.items.length > 0 && (
          <div className={`flex flex-col gap-3 ${isLoadingJobs ? "opacity-60" : ""}`}>
            {jobs.items.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                isSaving={savingIds.has(job.id)}
                onToggleSave={toggleSave}
              />
            ))}
          </div>
        )}

        {jobs && jobs.total_pages > 1 && (
          <nav className="flex items-center justify-between" aria-label="Pagination">
            <Button
              variant="secondary"
              disabled={page <= 1 || isLoadingJobs}
              onClick={() => setPage((p) => p - 1)}
            >
              Previous
            </Button>
            <span className="text-sm text-slate-600">
              Page {jobs.page} of {jobs.total_pages}
            </span>
            <Button
              variant="secondary"
              disabled={page >= jobs.total_pages || isLoadingJobs}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </nav>
        )}
      </section>
    </div>
  );
}
