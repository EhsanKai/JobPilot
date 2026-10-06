import type { MatchResult, MatchState, MatchSummary } from "@/types/match";

export function makeMatchSummary(overrides: Partial<MatchSummary> = {}): MatchSummary {
  return {
    status: "calculated",
    stale_reasons: [],
    overall_match: 87,
    job_match: 94,
    preference_match: 71,
    recommendation: "STRONG_MATCH",
    recommendation_label: "Strong match",
    band: { key: "strong", label: "Strong" },
    is_blocked: false,
    calculated_at: "2026-10-06T12:00:00Z",
    ...overrides,
  };
}

export function makeMatchResult(overrides: Partial<MatchResult> = {}): MatchResult {
  return {
    ...makeMatchSummary(),
    job_id: "job-1",
    user_job_id: "uj-1",
    overall_match_uncapped: 87,
    job_match_coverage: 0.83,
    preference_match_coverage: 0.9,
    coverage: 0.86,
    strong_matches: [
      { id: "skills:python", kind: "strong_match", component: "skills", group: "job",
        status: "match", importance: "required", text: "Python" },
      { id: "skills:aws", kind: "strong_match", component: "skills", group: "job",
        status: "match", importance: "unspecified", text: "Amazon Web Services" },
      { id: "experience", kind: "strong_match", component: "experience", group: "job",
        status: "match", importance: null, text: "6 years of experience (5+ asked)" },
      { id: "seniority", kind: "strong_match", component: "seniority", group: "job",
        status: "match", importance: null, text: "Senior role, your level" },
    ],
    gaps: [
      { id: "skills:kubernetes", kind: "gap", component: "skills", group: "job",
        status: "missing", importance: "preferred", text: "Kubernetes (nice to have)" },
    ],
    unknowns: [
      { id: "salary", kind: "unknown", component: "salary", group: "preference",
        status: "unknown", importance: null, text: "Salary not stated" },
    ],
    deal_breakers: [],
    components: [
      {
        key: "skills", group: "job", status: "match", label: null, score: 0.84, weight: 30,
        reason_code: "skills_compared", candidate: [], job: [], details: {},
        items: [
          {
            key: "python", label: "Python", importance: "required", status: "match", score: 1,
            reason_code: "skill_match",
            candidate: { value: "Python", origin: "user", ref: "skills/0", evidence_ref: null },
            job: { value: "Python", source_field: "description", section: "required",
                   text: "3+ years Python development", requirement_id: "r1" },
            group: null,
          },
        ],
      },
    ],
    deal_breaker_results: [],
    caps: [],
    versions: {
      matching_algorithm_version: "matching_v1",
      model_hash: "a".repeat(64),
      taxonomy_version: "taxonomy_v1",
      job_extractor_version: "job_extractor_v1",
      candidate_profile_version: 3,
      candidate_schema_version: "candidate_profile_v1",
    },
    ai_explanation: null,
    ...overrides,
  };
}

export function makeMatchState(overrides: Partial<MatchState> = {}): MatchState {
  return { job_id: "job-1", status: "calculated", result: makeMatchResult(), ...overrides };
}
