"""Tailored CV & Anschreiben generation (V0.8).

    facts.py        the confirmed resume (and the application's data) as citeable facts
    context.py      the deterministic generation context: what the AI is shown, and its hash
    prompts.py      explicit prompt versions
    claims.py       extracting the claims in generated text (numbers, terms, titles ...)
    validation.py   truth validation: structure + claims against the cited facts
    assembly.py     validated AI text + server-copied facts -> the stored document
    readiness.py    what must be done before a document can be generated
    service.py      the pipeline: context -> AI -> validation -> versioned document

The pipeline never lets AI output become a document without passing truth
validation, and never writes a fact about the candidate itself. See
docs/tailored-documents.md.
"""
