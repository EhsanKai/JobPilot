"""Deterministic normalization building blocks (V0.3 cleaning, V0.4 identity).

Two kinds of output, never confused:

- **Display values** -- cleaned text a person reads (`clean_text`,
  `canonicalize_url`, ...). Original wording, case and diacritics are kept.
- **Comparison keys** -- folded forms used only to decide whether two
  postings describe the same job (`title_key`, `company_key`,
  `location.key`, `url_identity_key`, ...). Never shown, never stored in
  place of the original.

    text.py         whitespace/HTML cleanup, Unicode folding, tokens, title keys
    url.py          safe URLs, display canonical URL, URL identity key
    company.py      company comparison key (legal suffixes)
    location.py     city / country / remote vocabulary and location keys
    salary.py       structured salary from amounts or text
    employment.py   employment type vocabulary
    work_model.py   work model vocabulary
    content.py      normalized content body and the content hash

No AI, no network, no randomness: the same input always gives the same output.
"""
