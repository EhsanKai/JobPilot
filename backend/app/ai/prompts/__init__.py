"""Versioned prompt resources.

Prompts live in plain-text files named `<name>_v<N>.txt` and are never
edited in place once released: a behavioral change gets a new version file
and a new parser version, so stored results always record exactly which
prompt produced them and old resumes can be re-processed with a newer one.
"""

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt resource '{name}' does not exist.")
    return path.read_text(encoding="utf-8").strip()
