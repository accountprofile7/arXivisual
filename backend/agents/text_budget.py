"""Static "text budget" gate for VISUAL_NARRATION videos (pure Python, no LLM).

Narrated "show, don't say" videos may carry only tiny tags on screen. This
parses the generated Manim code and counts the strings handed to text
mobjects: no Paragraph/BulletedList/Title, each Text at most
``MAX_WORDS_PER_TEXT`` words, and at most ``MAX_TAG_WORDS`` tag words across the
whole video. Math (MathTex/Tex) and purely numeric/symbolic strings ("256",
"1x1", "+") count as one word each.
"""

import ast
import re
from dataclasses import dataclass, field

MAX_WORDS_PER_TEXT = 3
MAX_TAG_WORDS = 15

BANNED_CALLS = ("Paragraph", "BulletedList", "Title")
WORD_TEXT_CALLS = ("Text", "MarkupText")
MATH_CALLS = ("Tex", "MathTex")

# A token with a run of 2+ letters is a word; "256", "1x1", "+", "⊕", "x" are not.
_WORD_RE = re.compile(r"[^\W\d_]{2,}")


def _is_symbolic(text: str) -> bool:
    return not _WORD_RE.search(text)


def _word_count(text: str) -> int:
    """Words in a tag; a purely numeric/symbolic string counts as one."""
    if not text.strip():
        return 0
    if _is_symbolic(text):
        return 1
    return len(text.split())


@dataclass
class TextBudgetResult:
    passed: bool
    tag_words: int = 0
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def get_feedback_message(self) -> str:
        lines = [
            "TEXT BUDGET EXCEEDED (show, don't say):",
            *[f"- {v}" for v in self.violations],
            f"Total tag words: {self.tag_words} (budget {MAX_TAG_WORDS}).",
            "Replace these with shapes/motion; keep tags <=3 words "
            f"(max {MAX_TAG_WORDS} tag words in the whole video; numbers, symbols and MathTex count as 1).",
        ]
        return "\n".join(lines)


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _literal_text(node: ast.expr) -> tuple[str | None, bool]:
    """(text, dynamic). text is None when nothing literal is known."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        text = "".join(parts)
        return (text if text.strip() else None), True
    return None, True


def _first_text_arg(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "text":
            return kw.value
    return None


def check_text_budget(code: str) -> TextBudgetResult:
    result = TextBudgetResult(passed=True)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        result.notes.append("code did not parse; text budget not checked")
        return result

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None:
            continue
        line = getattr(node, "lineno", "?")

        if name in BANNED_CALLS:
            result.violations.append(f"line {line}: {name}(...) is not allowed (no sentences, bullets or title cards)")
            continue
        if name not in WORD_TEXT_CALLS and name not in MATH_CALLS:
            continue

        if name in MATH_CALLS:
            result.tag_words += 1
            continue

        arg = _first_text_arg(node)
        if arg is None:
            continue
        text, dynamic = _literal_text(arg)
        if text is None:
            result.notes.append(f"line {line}: {name} text is dynamic; not counted")
            continue
        if dynamic:
            result.notes.append(f"line {line}: {name} f-string; only its literal parts are counted")
        words = _word_count(text)
        result.tag_words += words
        if words > MAX_WORDS_PER_TEXT:
            result.violations.append(f'line {line}: {name}("{text}") has {words} words (max {MAX_WORDS_PER_TEXT})')

    if result.tag_words > MAX_TAG_WORDS:
        result.violations.append(f"video has {result.tag_words} tag words in total (max {MAX_TAG_WORDS})")
    result.passed = not result.violations
    return result
