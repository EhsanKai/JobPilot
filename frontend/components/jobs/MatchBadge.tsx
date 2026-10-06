import { RECOMMENDATION_STYLES, percent } from "@/lib/match";
import type { MatchSummary } from "@/types/match";

/**
 * Compact match state for a job card. Never implies a score exists when none
 * was calculated, and always tells the Overall Match apart from its two parts.
 */
export function MatchBadge({ match }: { match: MatchSummary | null | undefined }) {
  if (!match) {
    return (
      <p className="text-sm text-slate-500" aria-label="Match">
        Match not calculated
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" aria-label="Match">
      {match.overall_match !== null && (
        <span className="font-semibold text-slate-900">{percent(match.overall_match)} Overall match</span>
      )}
      <span
        className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${RECOMMENDATION_STYLES[match.recommendation]}`}
      >
        {match.recommendation_label}
      </span>
      <span className="text-slate-600">
        Job match {percent(match.job_match)} · Preference match {percent(match.preference_match)}
      </span>
      {match.status === "stale" && (
        <span
          className="rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800"
          title="Calculated from information that has changed since. Recalculate on the job page."
        >
          Outdated
        </span>
      )}
    </div>
  );
}
