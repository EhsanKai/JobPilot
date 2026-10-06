"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { calculateMatch, explainMatch, getMatch } from "@/lib/api/match";
import { ApiError } from "@/lib/http";
import {
  COMPONENT_LABELS,
  COMPONENT_STATUS_LABELS,
  IMPORTANCE_LABELS,
  RECOMMENDATION_STYLES,
  percent,
  staleText,
} from "@/lib/match";
import type {
  AIExplanation,
  ComponentResult,
  ExplanationItem,
  MatchResult,
  MatchState,
} from "@/types/match";

const LIST_LIMIT = 6;

type Phase = "loading" | "ready" | "calculating" | "error";

/**
 * "Your match" on the job details page. States: not calculated, calculating,
 * calculated, stale (out of date) and error. The score always comes from
 * JobPilot's deterministic matching; the optional AI explanation only
 * explains it.
 */
export function MatchPanel({ jobId }: { jobId: string }) {
  const [state, setState] = useState<MatchState | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    setPhase("loading");
    setError(null);
    try {
      const result = await getMatch(jobId);
      if (!mounted.current) return;
      setState(result);
      setPhase("ready");
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof ApiError ? err.message : "Unable to load your match.");
      setPhase("error");
    }
  }, [jobId]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  async function calculate() {
    setPhase("calculating");
    setError(null);
    try {
      const result = await calculateMatch(jobId);
      if (!mounted.current) return;
      setState(result);
      setPhase("ready");
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof ApiError ? err.message : "Unable to calculate your match.");
      setPhase("error");
    }
  }

  const result = state?.result ?? null;

  return (
    <Card aria-labelledby="match-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="match-heading" className="text-lg font-semibold text-slate-900">
            Your match
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Calculated by JobPilot from your profile, preferences and confirmed resume — the same
            information always gives the same score.
          </p>
        </div>
        {phase !== "loading" && (state?.status === "not_calculated" || !state) && (
          <Button onClick={() => void calculate()} isLoading={phase === "calculating"}>
            Calculate match
          </Button>
        )}
        {state && state.status !== "not_calculated" && (
          <Button
            variant={state.status === "stale" ? "primary" : "secondary"}
            onClick={() => void calculate()}
            isLoading={phase === "calculating"}
          >
            Recalculate
          </Button>
        )}
      </div>

      {phase === "loading" && <p className="mt-4 text-sm text-slate-500">Loading your match...</p>}
      {phase === "calculating" && (
        <p className="mt-4 text-sm text-slate-500" role="status">
          Calculating your match...
        </p>
      )}
      {phase === "error" && error && (
        <div className="mt-4 flex flex-col gap-2">
          <Alert variant="error">{error}</Alert>
          <div>
            <Button variant="secondary" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        </div>
      )}
      {phase === "ready" && state?.status === "not_calculated" && (
        <p className="mt-4 text-sm text-slate-600">
          Match not calculated yet. Calculate it to see how well this job fits your skills and
          what you are looking for.
        </p>
      )}
      {result && phase !== "loading" && (
        <MatchDetails result={result} jobId={jobId} isStale={state?.status === "stale"} />
      )}
    </Card>
  );
}

function MatchDetails({
  result,
  jobId,
  isStale,
}: {
  result: MatchResult;
  jobId: string;
  isStale: boolean;
}) {
  const capped =
    result.overall_match !== null &&
    result.overall_match_uncapped !== null &&
    result.overall_match_uncapped > result.overall_match;
  return (
    <div className="mt-4 flex flex-col gap-5">
      {isStale && (
        <Alert variant="info">
          {staleText(result.stale_reasons)} The result below was calculated before; recalculate to
          update it.
        </Alert>
      )}

      <div className="flex flex-wrap items-end gap-6">
        <div>
          <p className="text-4xl font-semibold text-slate-900" aria-label="Overall match">
            {percent(result.overall_match)}
          </p>
          <p className="text-sm text-slate-500">
            Overall match{result.band ? ` · ${result.band.label}` : ""}
          </p>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <dt className="text-slate-500">Job match</dt>
          <dd className="font-medium text-slate-900" aria-label="Job match">
            {percent(result.job_match)}
          </dd>
          <dt className="text-slate-500">Preference match</dt>
          <dd className="font-medium text-slate-900" aria-label="Preference match">
            {percent(result.preference_match)}
          </dd>
        </dl>
        <span
          className={`rounded-full px-3 py-1 text-sm font-medium ${RECOMMENDATION_STYLES[result.recommendation]}`}
          aria-label="Recommendation"
        >
          {result.recommendation_label}
        </span>
      </div>
      <p className="text-xs text-slate-500">
        Job match: can you do this job (skills, experience, role, languages…). Preference match:
        does it fit what you want (location, work model, salary…). Overall = 70% job + 30%
        preference.
        {capped &&
          ` Capped at ${result.overall_match}% (calculated ${result.overall_match_uncapped}%) because ${
            result.is_blocked ? "a deal breaker applies" : "a required qualification is missing"
          }.`}
      </p>

      {result.deal_breakers.length > 0 && (
        <ExplanationList title="Deal breakers" items={result.deal_breakers} symbol="✕" tone="red" />
      )}
      <ExplanationList
        title="Why this matches"
        items={result.strong_matches}
        symbol="✓"
        tone="green"
        empty="No strong matches found."
      />
      <ExplanationList title="Potential gaps" items={result.gaps} symbol="⚠" tone="amber" />
      <ExplanationList title="Unknown" items={result.unknowns} symbol="?" tone="slate" />

      <AIExplanationSection jobId={jobId} stored={result.ai_explanation} disabled={isStale} />

      <details className="text-sm">
        <summary className="cursor-pointer font-medium text-brand-700">
          Show detailed evidence
        </summary>
        <EvidenceTable components={result.components} />
        <p className="mt-3 text-xs text-slate-400">
          {result.versions.matching_algorithm_version} · {result.versions.taxonomy_version} ·{" "}
          {result.versions.job_extractor_version} · profile version{" "}
          {result.versions.candidate_profile_version}
        </p>
      </details>
    </div>
  );
}

const TONES = {
  green: "text-emerald-700",
  amber: "text-amber-700",
  red: "text-red-700",
  slate: "text-slate-500",
};

function ExplanationList({
  title,
  items,
  symbol,
  tone,
  empty,
}: {
  title: string;
  items: ExplanationItem[];
  symbol: string;
  tone: keyof typeof TONES;
  empty?: string;
}) {
  const [showAll, setShowAll] = useState(false);
  if (items.length === 0 && !empty) return null;
  const shown = showAll ? items : items.slice(0, LIST_LIMIT);
  return (
    <section aria-label={title}>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-slate-500">{empty}</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1 text-sm text-slate-700">
          {shown.map((item) => (
            <li key={item.id} className="flex gap-2" data-kind={item.kind}>
              <span className={TONES[tone]} aria-hidden="true">
                {symbol}
              </span>
              <span>{item.text}</span>
            </li>
          ))}
        </ul>
      )}
      {items.length > LIST_LIMIT && (
        <button
          type="button"
          className="mt-1 text-xs font-medium text-brand-700 underline"
          onClick={() => setShowAll((value) => !value)}
        >
          {showAll ? "Show fewer" : `Show all ${items.length}`}
        </button>
      )}
    </section>
  );
}

function AIExplanationSection({
  jobId,
  stored,
  disabled,
}: {
  jobId: string;
  stored: AIExplanation | null;
  disabled: boolean;
}) {
  const [explanation, setExplanation] = useState<AIExplanation | null>(stored);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setExplanation(stored), [stored]);

  async function explain() {
    setIsLoading(true);
    setError(null);
    try {
      setExplanation(await explainMatch(jobId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The AI explanation isn't available.");
    } finally {
      setIsLoading(false);
    }
  }

  if (explanation) {
    return (
      <section aria-label="AI explanation" className="rounded-lg bg-slate-50 p-4 text-sm">
        <h3 className="font-semibold text-slate-900">In plain words</h3>
        <p className="mt-1 text-slate-700">{explanation.summary}</p>
        <p className="mt-2 text-xs text-slate-400">
          Written by AI ({explanation.ai_model ?? explanation.ai_provider}) from the facts above and
          checked against them. It doesn&apos;t change the score.
        </p>
      </section>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button
          variant="secondary"
          onClick={() => void explain()}
          isLoading={isLoading}
          disabled={disabled}
        >
          Explain in plain words (AI)
        </Button>
      </div>
      {error && <Alert variant="info">{error}</Alert>}
    </div>
  );
}

function EvidenceTable({ components }: { components: ComponentResult[] }) {
  return (
    <div className="mt-3 flex flex-col gap-3">
      {components.map((component) => (
        <div key={component.key} className="rounded-lg border border-slate-100 p-3">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-900">
              {COMPONENT_LABELS[component.key] ?? component.key}
            </span>
            <span className="text-xs text-slate-500">
              {component.group === "job" ? "Job match" : "Preference match"} · weight{" "}
              {component.weight}
            </span>
            <span className="text-xs font-medium text-slate-700">
              {COMPONENT_STATUS_LABELS[component.status] ?? component.status}
              {component.score !== null ? ` (${Math.round(component.score * 100)}%)` : ""}
            </span>
          </p>
          {component.candidate.length > 0 && (
            <p className="mt-1 text-xs text-slate-600">
              You: {component.candidate.map((e) => e.value).join(", ")}
            </p>
          )}
          {component.job.length > 0 && (
            <p className="mt-1 text-xs text-slate-600">
              Job: {component.job.map((e) => e.value).join(", ")}
            </p>
          )}
          {component.items.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1 text-xs text-slate-600">
              {component.items.map((item) => (
                <li key={`${item.key}-${item.importance}`}>
                  <span className="font-medium text-slate-800">{item.label}</span>
                  {item.importance ? ` · ${IMPORTANCE_LABELS[item.importance]}` : ""} ·{" "}
                  {COMPONENT_STATUS_LABELS[item.status] ?? item.status}
                  {item.job?.text ? (
                    <span className="block text-slate-400">&ldquo;{item.job.text}&rdquo;</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
