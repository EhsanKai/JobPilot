/** Job Matching & Explainability (V0.6). Mirrors backend/app/schemas/matching.py. */

export type MatchStatus = "not_calculated" | "calculated" | "stale";
export type StaleReason =
  | "profile_changed"
  | "job_changed"
  | "algorithm_changed"
  | "taxonomy_changed";
export type Recommendation =
  | "STRONG_MATCH"
  | "GOOD_MATCH"
  | "POTENTIAL_MATCH"
  | "WEAK_MATCH"
  | "NOT_RECOMMENDED"
  | "INSUFFICIENT_DATA";

export interface ScoreBand {
  key: string;
  label: string;
}

/** Compact match state shown on job cards; `null` on a job = not calculated. */
export interface MatchSummary {
  status: "calculated" | "stale";
  stale_reasons: StaleReason[];
  overall_match: number | null;
  job_match: number | null;
  preference_match: number | null;
  recommendation: Recommendation;
  recommendation_label: string;
  band: ScoreBand | null;
  is_blocked: boolean;
  calculated_at: string;
}

export type ExplanationKind = "strong_match" | "gap" | "unknown" | "deal_breaker";

export interface ExplanationItem {
  id: string;
  kind: ExplanationKind;
  component: string;
  group: string;
  status: string;
  importance: string | null;
  text: string;
}

export interface CandidateEvidence {
  value: string;
  origin: "user" | "resume" | null;
  ref: string | null;
  evidence_ref: string | null;
}

export interface JobEvidence {
  value: string;
  source_field: string | null;
  section: string | null;
  text: string | null;
  requirement_id: string | null;
}

export interface ItemResult {
  key: string;
  label: string;
  importance: "required" | "preferred" | "unspecified" | "contextual" | null;
  status: "match" | "partial" | "missing" | "unknown";
  score: number | null;
  reason_code: string;
  candidate: CandidateEvidence | null;
  job: JobEvidence | null;
  group: string | null;
}

export interface ComponentResult {
  key: string;
  group: "job" | "preference";
  status: "match" | "partial" | "mismatch" | "unknown";
  label: string | null;
  score: number | null;
  weight: number;
  reason_code: string;
  candidate: CandidateEvidence[];
  job: JobEvidence[];
  items: ItemResult[];
  details: Record<string, string | number | boolean | null>;
}

export interface MatchCap {
  key: string;
  max_score: number;
  blocking: boolean;
  reason_code: string;
  criterion_key: string | null;
}

export interface ExplainedPoint {
  fact_id: string;
  text: string;
}

export interface AIExplanation {
  prompt_version: string;
  ai_provider: string;
  ai_model: string | null;
  summary: string;
  strengths: ExplainedPoint[];
  concerns: ExplainedPoint[];
  created_at: string;
}

export interface MatchResult extends MatchSummary {
  job_id: string;
  user_job_id: string;
  overall_match_uncapped: number | null;
  job_match_coverage: number | null;
  preference_match_coverage: number | null;
  coverage: number;
  strong_matches: ExplanationItem[];
  gaps: ExplanationItem[];
  unknowns: ExplanationItem[];
  deal_breakers: ExplanationItem[];
  components: ComponentResult[];
  deal_breaker_results: {
    type: string;
    source: string;
    status: string;
    reason_code: string;
  }[];
  caps: MatchCap[];
  versions: {
    matching_algorithm_version: string;
    model_hash: string;
    taxonomy_version: string | null;
    job_extractor_version: string;
    candidate_profile_version: number;
    candidate_schema_version: string;
  };
  ai_explanation: AIExplanation | null;
}

export interface MatchState {
  job_id: string;
  status: MatchStatus;
  result: MatchResult | null;
}

export interface BatchMatchResponse {
  results: { job_id: string; outcome: "calculated" | "unchanged"; match: MatchSummary }[];
  calculated: number;
  unchanged: number;
  not_found: string[];
}
