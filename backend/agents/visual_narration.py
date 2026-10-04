"""VISUAL_NARRATION flag: planner/generator prompt blocks that make narrated
videos DRAW the quantities and structures they name (default off).

The flag is read at call time so tests (and the dev runner) can toggle it per
process. With it off, no prompt text changes.
"""

import os
from pathlib import Path

_PROMPTS = Path(__file__).parent.parent / "prompts"

# Where each block goes: inserted just before this heading in its template.
PLANNER_ANCHOR = "## Output Format (JSON only)"
GENERATOR_ANCHOR = "## Output Contract"

VISUAL_AID_MARKER = "VISUAL AID:"
VISUAL_AID_MOMENTS = 3


def visual_narration_enabled() -> bool:
    return os.getenv("VISUAL_NARRATION", "").strip().lower() in ("1", "true")


def load_block(filename: str) -> str:
    return (_PROMPTS / filename).read_text(encoding="utf-8").strip()


def insert_block(template: str, anchor: str, block: str) -> str:
    """Insert ``block`` before ``anchor`` in ``template`` (appended if absent)."""
    if anchor in template:
        return template.replace(anchor, block + "\n\n" + anchor, 1)
    return template.rstrip() + "\n\n" + block + "\n"
