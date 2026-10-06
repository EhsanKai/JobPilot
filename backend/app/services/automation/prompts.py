"""The prompt of the AI field-to-question mapping (V0.10).

Versioned and fingerprinted like the V0.8 / V0.9 prompts: the version and hash are
stored with every AI mapping (`AutomationField.mapping_provenance`), a released prompt
file is never edited, and `PROMPT_FINGERPRINTS` (checked by a test) makes a silent
edit fail loudly.
"""

import json
from typing import Any

from app.ai.prompts import load_prompt, prompt_hash

FIELD_MAPPING_PROMPT_VERSION = "field_mapping_v1"

PROMPT_FINGERPRINTS: dict[str, str] = {
    FIELD_MAPPING_PROMPT_VERSION: (
        "8b897db7ac73eebc97639aeb270fd5c9a7a18a43440e0ac51b9e11ca701b3575"
    ),
}


def system_prompt() -> str:
    return load_prompt(FIELD_MAPPING_PROMPT_VERSION)


def current_prompt_hash() -> str:
    return prompt_hash(FIELD_MAPPING_PROMPT_VERSION)


def user_prompt(context: dict[str, Any]) -> str:
    return (
        "Match the form field to at most one candidate question.\n\n<mapping_context>\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n</mapping_context>"
    )
