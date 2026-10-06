"""Versioned prompt resources.

Prompts live in plain-text files named `<name>_v<N>.txt` and are never
edited in place once released: a behavioral change gets a new version file
and a new parser version, so stored results always record exactly which
prompt produced them and old resumes can be re-processed with a newer one.
"""

import hashlib
from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt resource '{name}' does not exist.")
    return path.read_text(encoding="utf-8").strip()


@cache
def prompt_hash(name: str) -> str:
    """SHA-256 of the released prompt file, as stored in a result's
    provenance. Hashes the file's bytes, so any edit changes it."""
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt resource '{name}' does not exist.")
    return hashlib.sha256(path.read_bytes()).hexdigest()
