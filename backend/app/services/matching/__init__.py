"""Job Matching & Explainability (V0.6).

    candidate.py    V0.1 profile + confirmed Resume Profile -> candidate snapshot
    extraction.py   canonical Job -> JobFacts + requirements (deterministic)
    terms.py        V0.5.1 taxonomy terms inside posting prose
    model.py        the `matching_v1` scoring model (weights, thresholds; hashed)
    components.py   one deterministic evaluator per component, with evidence
    engine.py       components -> Job / Preference / Overall Match -> recommendation
    explanation.py  deterministic summary; optional, validated AI explanation
    status.py       staleness and API read models
    service.py      database orchestration (snapshots, intelligence, results)

The score is never produced by an AI. See docs/job-matching.md.
"""
