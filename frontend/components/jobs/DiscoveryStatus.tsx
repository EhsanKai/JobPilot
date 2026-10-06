import { Alert } from "@/components/ui/Alert";
import type { DiscoveryRun, SearchFilter } from "@/types/jobs";

const FILTER_LABELS: Record<SearchFilter, string> = {
  keywords: "keywords",
  job_titles: "job titles",
  locations: "locations",
  work_models: "work model",
  employment_types: "employment type",
  industries: "industries",
  salary: "salary",
  posted_after: "posting date",
};

interface DiscoveryStatusProps {
  run: DiscoveryRun | null;
  /** Error starting or polling the run (network, 409, ...). */
  error: string | null;
}

/** Explicit state of the latest discovery run. */
export function DiscoveryStatus({ run, error }: DiscoveryStatusProps) {
  if (error) return <Alert variant="error">{error}</Alert>;
  if (!run) return null;

  if (run.is_active) {
    return (
      <div role="status" className="flex items-center gap-3 rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-800">
        <span
          className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
          aria-hidden="true"
        />
        Discovering jobs... This can take a moment. You can keep browsing.
      </div>
    );
  }

  const summary = run.summary;
  const failedSources = summary?.sources.filter((s) => s.status !== "succeeded") ?? [];
  const notApplied = Array.from(
    new Set(
      summary?.sources.flatMap((s) =>
        s.filters.filter((f) => f.applied_by === "not_applied").map((f) => f.filter)
      ) ?? []
    )
  );

  if (run.status === "failed") {
    return (
      <Alert variant="error">
        <p className="font-medium">Unable to reach the job sources.</p>
        <p>{run.error_message ?? "Job discovery failed. Please try again later."}</p>
        <SourceErrors run={run} />
      </Alert>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {run.status === "partial" && (
        <Alert variant="error">
          <p className="font-medium">Some sources succeeded, some failed.</p>
          <p>
            Results from {failedSources.map((s) => s.display_name).join(", ")} may be incomplete.
          </p>
          <SourceErrors run={run} />
        </Alert>
      )}
      {summary && (
        <Alert variant="success">
          {summary.discovered === 0
            ? "Discovery finished: no matching jobs found this time."
            : `Discovery finished: ${summary.discovered} matching job${summary.discovered === 1 ? "" : "s"} (${summary.created} new, ${summary.updated} updated).`}
          {summary.merged > 0 &&
            ` ${summary.merged} ${summary.merged === 1 ? "posting was" : "postings were"} recognized as the same job on another source.`}
          {summary.possible_matches > 0 &&
            ` ${summary.possible_matches} similar ${summary.possible_matches === 1 ? "posting was" : "postings were"} kept separate to be safe.`}
          {summary.beyond_limit > 0 &&
            ` ${summary.beyond_limit} more matched beyond this run's limit.`}
        </Alert>
      )}
      {notApplied.length > 0 && (
        <Alert variant="info">
          No source could filter by {notApplied.map((f) => FILTER_LABELS[f]).join(", ")}, so those
          filters were not applied.
        </Alert>
      )}
    </div>
  );
}

function SourceErrors({ run }: { run: DiscoveryRun }) {
  const errors =
    run.summary?.sources.flatMap((source) =>
      source.errors.map((error) => ({ source: source.display_name, ...error }))
    ) ?? [];
  if (errors.length === 0) return null;
  return (
    <ul className="mt-1 list-disc pl-5">
      {errors.map((error, index) => (
        <li key={`${error.source}-${error.target}-${index}`}>
          {error.source}
          {error.target ? ` (${error.target})` : ""}: {error.message}
        </li>
      ))}
    </ul>
  );
}
