"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/tracking/StatusBadge";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { listMyApplications } from "@/lib/api/tracking";
import { ApiError } from "@/lib/http";
import { safeExternalUrl, sourceLabel } from "@/lib/jobs";
import { ALL_STATUSES, dateText, statusLabel, submissionText } from "@/lib/tracking";
import type {
  ApplicationList,
  ApplicationListQuery,
  ApplicationSort,
  ApplicationStatus,
  ApplicationSummary,
} from "@/types/application";

const PAGE_SIZE = 20;
const SELECT_CLASS =
  "rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500";

/** What the filter form holds, as the user typed it. */
interface Filters {
  status: "" | ApplicationStatus;
  company: string;
  source: string;
  appliedFrom: string;
  appliedTo: string;
  sort: ApplicationSort;
}

const NO_FILTERS: Filters = {
  status: "",
  company: "",
  source: "",
  appliedFrom: "",
  appliedTo: "",
  sort: "updated",
};

function toQuery(filters: Filters, page: number): ApplicationListQuery {
  return {
    page,
    pageSize: PAGE_SIZE,
    statuses: filters.status ? [filters.status] : [],
    company: filters.company,
    source: filters.source,
    appliedFrom: filters.appliedFrom,
    appliedTo: filters.appliedTo,
    sort: filters.sort,
  };
}

function isFiltered(filters: Filters): boolean {
  return Boolean(
    filters.status ||
      filters.company.trim() ||
      filters.source.trim() ||
      filters.appliedFrom ||
      filters.appliedTo
  );
}

/**
 * "My Applications": every application the user has, with where it stands. The server
 * does the filtering and sorting (so paging stays correct); this only shows the result.
 */
export function MyApplications() {
  // `form` is what is being typed; `applied` is what the list was asked for.
  const [form, setForm] = useState<Filters>(NO_FILTERS);
  const [applied, setApplied] = useState<Filters>(NO_FILTERS);
  const [page, setPage] = useState(1);
  const [list, setList] = useState<ApplicationList | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    setError(null);
    listMyApplications(toQuery(applied, page))
      .then((loaded) => isMounted && setList(loaded))
      .catch((err) => {
        if (!isMounted) return;
        setError(
          err instanceof ApiError && err.status === 422
            ? "Those filters aren't valid. Check the dates and try again."
            : "Unable to load your applications. Please try again."
        );
      })
      .finally(() => isMounted && setIsLoading(false));
    return () => {
      isMounted = false;
    };
  }, [applied, page, reloadKey]);

  function apply(next: Filters) {
    setApplied(next);
    setPage(1);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    apply(form);
  }

  /** Status and sort take effect at once; the typed fields wait for "Apply filters". */
  function changeImmediately<K extends "status" | "sort">(key: K, value: Filters[K]) {
    const next = { ...form, [key]: value };
    setForm(next);
    apply({ ...applied, [key]: value });
  }

  function clear() {
    setForm(NO_FILTERS);
    apply(NO_FILTERS);
  }

  const retry = useCallback(() => setReloadKey((key) => key + 1), []);
  const filtered = isFiltered(applied);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">My Applications</h1>
        <p className="mt-1 text-slate-500">
          Every application you have started, and where it stands. You change a status yourself:
          JobPilot only moves it to Applied after a confirmed submission.
        </p>
      </div>

      <Card>
        <form onSubmit={handleSubmit} aria-label="Filter applications" className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex flex-col gap-1">
              <label htmlFor="filter-status" className="text-sm font-medium text-slate-700">
                Status
              </label>
              <select
                id="filter-status"
                className={SELECT_CLASS}
                value={form.status}
                onChange={(event) =>
                  changeImmediately("status", event.target.value as Filters["status"])
                }
              >
                <option value="">All statuses</option>
                {ALL_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {statusLabel(status)}
                  </option>
                ))}
              </select>
            </div>
            <Input
              label="Company"
              name="filter-company"
              value={form.company}
              onChange={(event) => setForm({ ...form, company: event.target.value })}
            />
            <div className="flex flex-col gap-1">
              <Input
                label="Source"
                name="filter-source"
                list="source-options"
                placeholder="e.g. greenhouse"
                value={form.source}
                onChange={(event) => setForm({ ...form, source: event.target.value })}
              />
              <datalist id="source-options">
                <option value="greenhouse">{sourceLabel("greenhouse")}</option>
                <option value="lever">{sourceLabel("lever")}</option>
              </datalist>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="sort" className="text-sm font-medium text-slate-700">
                Sort by
              </label>
              <select
                id="sort"
                className={SELECT_CLASS}
                value={form.sort}
                onChange={(event) => changeImmediately("sort", event.target.value as ApplicationSort)}
              >
                <option value="updated">Last updated</option>
                <option value="applied">Application date</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Input
              label="Applied from"
              name="filter-applied-from"
              type="date"
              value={form.appliedFrom}
              onChange={(event) => setForm({ ...form, appliedFrom: event.target.value })}
            />
            <Input
              label="Applied to"
              name="filter-applied-to"
              type="date"
              value={form.appliedTo}
              onChange={(event) => setForm({ ...form, appliedTo: event.target.value })}
            />
            <div className="flex items-end gap-2 sm:col-span-2">
              <Button type="submit">Apply filters</Button>
              <Button type="button" variant="secondary" onClick={clear}>
                Clear filters
              </Button>
            </div>
          </div>
        </form>
      </Card>

      {error && (
        <div className="flex flex-col items-start gap-3">
          <Alert variant="error">{error}</Alert>
          <Button variant="secondary" onClick={retry}>
            Try again
          </Button>
        </div>
      )}

      {isLoading && !list && (
        <p role="status" className="text-sm text-slate-500">
          Loading your applications...
        </p>
      )}

      {list && !error && (
        <div className="flex flex-col gap-4" aria-busy={isLoading}>
          {list.total === 0 ? (
            <EmptyState filtered={filtered} onClear={clear} />
          ) : (
            <>
              <p className="text-sm text-slate-500" aria-live="polite">
                {list.total} {list.total === 1 ? "application" : "applications"}
                {filtered ? " match your filters" : ""}
              </p>
              <ul className="flex flex-col gap-3" aria-label="Applications">
                {list.items.map((item) => (
                  <li key={item.id}>
                    <ApplicationRow item={item} />
                  </li>
                ))}
              </ul>
              {list.total_pages > 1 && (
                <nav aria-label="Pages" className="flex items-center justify-between">
                  <Button
                    variant="secondary"
                    onClick={() => setPage(page - 1)}
                    disabled={page <= 1 || isLoading}
                  >
                    Previous
                  </Button>
                  <span className="text-sm text-slate-500">
                    Page {list.page} of {list.total_pages}
                  </span>
                  <Button
                    variant="secondary"
                    onClick={() => setPage(page + 1)}
                    disabled={page >= list.total_pages || isLoading}
                  >
                    Next
                  </Button>
                </nav>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function EmptyState({ filtered, onClear }: { filtered: boolean; onClear: () => void }) {
  if (filtered) {
    return (
      <Card className="flex flex-col items-start gap-3">
        <p className="font-medium text-slate-900">No applications match your filters.</p>
        <Button variant="secondary" onClick={onClear}>
          Clear filters
        </Button>
      </Card>
    );
  }
  return (
    <Card className="flex flex-col items-start gap-3">
      <p className="font-medium text-slate-900">You haven&apos;t started an application yet.</p>
      <p className="text-sm text-slate-500">
        Open a job and choose Start Application: it will appear here.
      </p>
      <Link
        href="/jobs"
        className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
      >
        Find jobs
      </Link>
    </Card>
  );
}

function ApplicationRow({ item }: { item: ApplicationSummary }) {
  const originalUrl = safeExternalUrl(item.original_url);
  const submission = submissionText(item.submission);
  return (
    <Card className="flex flex-col gap-3 !p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900">
            <Link
              href={`/applications/${item.id}/tracking`}
              className="hover:text-brand-700 hover:underline"
            >
              {item.company_name} — {item.title}
            </Link>
          </h2>
          <p className="text-sm text-slate-500">
            {sourceLabel(item.source)}
            {item.location ? ` · ${item.location}` : ""}
          </p>
        </div>
        <StatusBadge status={item.status} />
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-slate-500">Applied</dt>
          <dd className="text-slate-900">{dateText(item.applied_at, "Not applied yet")}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Last status change</dt>
          <dd className="text-slate-900">{dateText(item.last_status_change_at)}</dd>
        </div>
        {item.follow_up_at && (
          <div>
            <dt className="text-slate-500">Follow up on</dt>
            <dd className="text-slate-900">{dateText(item.follow_up_at)}</dd>
          </div>
        )}
        {submission && (
          <div className="col-span-2">
            <dt className="text-slate-500">Submission</dt>
            <dd className="text-slate-900">{submission}</dd>
          </div>
        )}
      </dl>

      <div className="flex flex-wrap gap-4 text-sm font-medium">
        <Link href={`/applications/${item.id}/tracking`} className="text-brand-700 hover:underline">
          Timeline &amp; status
        </Link>
        <Link href={`/applications/${item.id}`} className="text-brand-700 hover:underline">
          Open workspace
        </Link>
        {originalUrl && (
          <a
            href={originalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-700 hover:underline"
          >
            Original job posting ↗
          </a>
        )}
      </div>
    </Card>
  );
}
