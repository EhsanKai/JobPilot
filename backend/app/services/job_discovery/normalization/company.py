"""Company name normalization -- a comparison aid only.

The original company name is always kept for display (`Job.company_name`,
`JobSourcePosting.company_name`); `company_key` is what deduplication
compares:

    "Example GmbH", "EXAMPLE GMBH", "Example GmbH."  ->  "example"
    "Example Systems GmbH"                          ->  "example systems"

Only *trailing* legal-form suffixes are removed, only when something is left
afterwards, and keys are compared for exact equality -- never fuzzily. Words
such as "Group", "Holding", "Systems" or "Digital" are kept: they often
distinguish separate legal entities.
"""

from app.services.job_discovery.normalization.text import tokens

# Token sequences (already folded: "GmbH & Co. KG" -> gmbh and co kg).
# Longest sequences first so "gmbh and co kg" is removed as one suffix.
LEGAL_SUFFIXES: tuple[tuple[str, ...], ...] = tuple(
    sorted(
        {
            ("gmbh", "and", "co", "kg"),
            ("gmbh", "co", "kg"),
            ("ag", "and", "co", "kg"),
            ("se", "and", "co", "kg"),
            ("and", "co", "kg"),
            ("co", "kg"),
            ("and", "co"),
            ("ug", "haftungsbeschrankt"),
            ("gmbh",),
            ("ggmbh",),
            ("mbh",),
            ("ag",),
            ("se",),
            ("kg",),
            ("kgaa",),
            ("ug",),
            ("ohg",),
            ("e", "v"),
            ("ev",),
            ("ltd",),
            ("limited",),
            ("inc",),
            ("incorporated",),
            ("llc",),
            ("llp",),
            ("plc",),
            ("corp",),
            ("corporation",),
            ("co",),
            ("pty", "ltd"),
            ("pty",),
            ("b", "v"),
            ("bv",),
            ("n", "v"),
            ("nv",),
            ("s", "a"),
            ("sa",),
            ("sas",),
            ("sarl",),
            ("s", "r", "l"),
            ("srl",),
            ("s", "p", "a"),
            ("spa",),
            ("oy",),
            ("oyj",),
            ("ab",),
            ("asa",),
            ("aps",),
            ("sp", "z", "o", "o"),
        },
        key=lambda suffix: (-len(suffix), suffix),
    )
)


def company_key(name: str | None) -> str:
    """Comparison key for a company name (see module docs)."""
    words = tokens(name)
    stripped = True
    while stripped:
        stripped = False
        for suffix in LEGAL_SUFFIXES:
            n = len(suffix)
            if len(words) > n and tuple(words[-n:]) == suffix:
                words = words[:-n]
                stripped = True
                break
    return " ".join(words)
