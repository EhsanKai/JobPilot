"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/Card";
import { getStatusCounts } from "@/lib/api/tracking";
import { summaryRows } from "@/lib/tracking";
import type { StatusCounts } from "@/types/application";

/**
 * A compact count of the user's applications per status, with a link to "My Applications".
 * Counts only: no analytics. If the counts can't be loaded the card still links to the list.
 */
export function ApplicationsSummaryCard() {
  const [counts, setCounts] = useState<StatusCounts | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let isMounted = true;
    getStatusCounts()
      .then((loaded) => isMounted && setCounts(loaded))
      .catch(() => isMounted && setFailed(true))
      .finally(() => isMounted && setIsLoading(false));
    return () => {
      isMounted = false;
    };
  }, []);

  const rows = counts ? summaryRows(counts) : [];

  return (
    <Card aria-labelledby="applications-summary-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="applications-summary-heading" className="font-medium text-slate-900">
            Applications
          </h2>
          {isLoading && <p className="mt-2 text-sm text-slate-500">Loading...</p>}
          {failed && (
            <p className="mt-2 text-sm text-slate-500">Your application counts are unavailable.</p>
          )}
          {counts && counts.total === 0 && (
            <p className="mt-2 text-sm text-slate-500">You haven&apos;t started an application yet.</p>
          )}
          {counts && counts.total > 0 && rows.length === 0 && (
            <p className="mt-2 text-sm text-slate-500">
              {counts.total} {counts.total === 1 ? "application" : "applications"}, none submitted yet.
            </p>
          )}
          {rows.length > 0 && (
            <dl className="mt-2 grid grid-cols-[auto_auto] gap-x-6 gap-y-1 text-sm">
              {rows.map((row) => (
                <div key={row.status} className="contents">
                  <dt className="text-slate-600">{row.label}</dt>
                  <dd className="text-right font-medium text-slate-900">{row.count}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
        <Link
          href="/applications"
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          My Applications
        </Link>
      </div>
    </Card>
  );
}
