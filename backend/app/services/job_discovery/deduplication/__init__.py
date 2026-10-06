"""Cross-source identity resolution (V0.4).

Answers one question, deterministically and conservatively: *do these two
source postings describe the same real-world job?*

    identity.py     comparison keys of a posting, identity locks
    similarity.py   description similarity (word 3-gram Jaccard)
    resolver.py     the decision model: EXACT / HIGH_CONFIDENCE /
                    POSSIBLE_MATCH / NO_MATCH, and candidate lookup
    merger.py       preferred posting policy, building the canonical Job
                    from its postings

Only EXACT and HIGH_CONFIDENCE merge automatically. Anything less keeps the
postings as separate jobs: showing a duplicate is cheap, attaching a user's
application to the wrong vacancy is not. See docs/job-intelligence.md.
"""
