"""V0.2 Resume Intelligence: resume file -> reviewable, evidence-backed Resume Profile.

Pipeline stages, one module each:

    extraction.py          file bytes -> normalized text (PDF/DOCX, OCR detection)
    parser.py              text -> ParsedResume (provider-agnostic AI parser)
    validation.py          ParsedResume -> ResumeProfileData (truth checks, evidence)
    processing_service.py  orchestration, status transitions, persistence, reuse
    review_service.py      accept / edit / reject / confirm
    conflicts.py           resume vs. user profile comparison (read-only)
"""
