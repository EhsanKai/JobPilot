"""Prompt versions and prompt building for tailored documents (V0.8).

Each document type has an explicit, versioned prompt resource
(`app/ai/prompts/<version>.txt`). The version is stored with every generated
document, so a document always says which prompt produced it. A released
prompt file is never edited: a behavioral change gets a new file and a new
version constant, and `PROMPT_FINGERPRINTS` (checked by a test) makes a silent
edit fail loudly.

The prompt text itself is not stored with the document, and it is never
returned by the API.
"""

import json

from app.ai.prompts import load_prompt
from app.models.generated_document import GeneratedDocumentType
from app.services.documents.context import GenerationContext

TAILORED_CV_PROMPT_VERSION = "tailored_cv_v1"
COVER_LETTER_PROMPT_VERSION = "cover_letter_v1"

#: V0.9: reword a validated letter for a natural tone (a new version, validated like any).
HUMANIZE_PROMPT_VERSION = "cover_letter_humanize_v1"

PROMPT_VERSIONS: dict[GeneratedDocumentType, str] = {
    GeneratedDocumentType.TAILORED_CV: TAILORED_CV_PROMPT_VERSION,
    GeneratedDocumentType.COVER_LETTER: COVER_LETTER_PROMPT_VERSION,
}

#: SHA-256 of each released prompt file. Changing a released prompt requires a
#: new version (a new file and a new entry here), never an edit in place.
PROMPT_FINGERPRINTS: dict[str, str] = {
    TAILORED_CV_PROMPT_VERSION: "5a055b22995b668083c863c895580170ec42dbfeb7cc50334619cc1453faea80",
    COVER_LETTER_PROMPT_VERSION: "2c27ab7867e9ee9774324276ed95b443f5f65b9f6e74c09a32b606b045b89ef8",
    HUMANIZE_PROMPT_VERSION: "0cb0696576959a9d3c15fc3d503372c7a3053b220fbbf9cf73c7d61fcdb06ad1",
}

_TASKS = {
    GeneratedDocumentType.TAILORED_CV: "Tailor the candidate's CV for the target job",
    GeneratedDocumentType.COVER_LETTER: (
        "Write the body of the candidate's cover letter for the target job"
    ),
}


def system_prompt(document_type: GeneratedDocumentType) -> str:
    return load_prompt(PROMPT_VERSIONS[document_type])


def user_prompt(context: GenerationContext) -> str:
    """The generation context, as data between tags."""
    language = context.payload["output_language"]["name"]
    return (
        f"{_TASKS[context.document_type]}, using ONLY the facts between the "
        f"<generation_context> tags. Write in {language}.\n\n<generation_context>\n"
        f"{json.dumps(context.payload, ensure_ascii=False, indent=2)}\n"
        "</generation_context>"
    )
