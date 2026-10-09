"""Static "no frames" gate for VISUAL_NARRATION videos (pure Python, no LLM).

Narrated videos keep a clean center stage: no outline, frame or placeholder
box around anything. Generated scenes kept drawing one anyway, so this parses
the Manim code and flags:

- R1: ``DashedVMobject`` of a rectangle-family shape (directly, through a
  method chain such as ``Rectangle(...).move_to(...)``, or through a name
  bound to one) - a dashed box, frame or placeholder.
- R2: any ``SurroundingRectangle(...)`` - a frame around existing mobjects.
  Highlights use ``Indicate`` / ``Circumscribe`` / ``set_stroke`` instead.

Filled panels (e.g. a ``RoundedRectangle`` card) and dashed lines are fine.
"""

import ast
import logging
from dataclasses import dataclass, field

RECT_SHAPES = ("Rectangle", "Square", "RoundedRectangle", "ScreenRectangle", "SurroundingRectangle")

logger = logging.getLogger(__name__)


@dataclass
class FrameCheckResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def get_feedback_message(self) -> str:
        lines = [
            "FRAMES ARE NOT ALLOWED (no outline, box or placeholder around anything):",
            *[f"- {v}" for v in self.violations],
            "Delete these. Group parts by spacing and color; highlight with Indicate, Circumscribe "
            "or .animate.set_stroke(...) instead. Start the diagram with the real input shape, not an outline.",
        ]
        return "\n".join(lines)


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _root_call(node: ast.expr) -> ast.expr:
    """``Rectangle(...).move_to(...).set_stroke(...)`` -> ``Rectangle(...)``."""
    while (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Call)
    ):
        node = node.func.value
    return node


def _short(node: ast.expr, limit: int = 60) -> str:
    try:
        text = ast.unparse(node)
    except Exception:
        return "..."
    return text if len(text) <= limit else text[: limit - 3] + "..."


class _Checker:
    def __init__(self, tree: ast.AST):
        self.tree = tree
        # name -> its assignments in source order; assigned value -> its target name
        self.bindings: dict[str, list[ast.Assign]] = {}
        self.target_of: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                self.bindings.setdefault(name, []).append(node)
                self.target_of[id(node.value)] = name
        for assigns in self.bindings.values():
            assigns.sort(key=lambda a: (a.lineno, a.col_offset))
        self.parents: dict[int, ast.AST] = {
            id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }

    def outermost(self, call: ast.Call) -> ast.expr:
        """The full chain ``call`` heads, e.g. ``SurroundingRectangle(x).set_stroke(...)``."""
        node: ast.expr = call
        while True:
            attr = self.parents.get(id(node))
            outer = self.parents.get(id(attr))
            if not (
                isinstance(attr, ast.Attribute) and attr.value is node
                and isinstance(outer, ast.Call) and outer.func is attr
            ):
                return node
            node = outer

    def binding_at(self, name: ast.Name) -> ast.expr | None:
        """The value last assigned to ``name`` above where it is read (scopes are not tracked)."""
        before = [
            a for a in self.bindings.get(name.id, [])
            if (a.end_lineno, a.end_col_offset) <= (name.lineno, name.col_offset)
        ]
        return before[-1].value if before else None

    def rect_shape(self, node: ast.expr) -> str | None:
        """The rectangle-family shape ``node`` evaluates to, if any.

        Each name step moves to an assignment that ends above it, so this
        always terminates and does linear work.
        """
        if isinstance(node, ast.Name):
            value = self.binding_at(node)
            return self.rect_shape(value) if value is not None else None
        root = _root_call(node)
        if isinstance(root, ast.Call):
            name = _call_name(root)
            if name in RECT_SHAPES:
                return name
            # ``b.copy()`` / ``b.scale(1.2)``: a method chain on a name.
            if isinstance(root.func, ast.Attribute) and isinstance(root.func.value, ast.Name):
                return self.rect_shape(root.func.value)
        return None

    def run(self) -> FrameCheckResult:
        result = FrameCheckResult(passed=True)
        found: list[tuple[int, str]] = []
        reported: set[int] = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "DashedVMobject":
                continue
            arg = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg == "vmobject"), None
            )
            if arg is None:
                continue
            shape = self.rect_shape(arg)
            if shape is None:
                continue
            reported.add(id(_root_call(arg)))
            via = "" if isinstance(_root_call(arg), ast.Call) else f" ({_short(arg)} is a {shape})"
            found.append((
                node.lineno,
                f"line {node.lineno}: DashedVMobject({_short(arg)}) draws a dashed box or placeholder{via}",
            ))
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "SurroundingRectangle":
                continue
            if id(node) in reported:
                continue
            target = self.target_of.get(id(self.outermost(node)))
            label = f"{target} = " if target else ""
            found.append((node.lineno, f"line {node.lineno}: {label}SurroundingRectangle(...) draws a frame"))
        result.violations = [msg for _, msg in sorted(found, key=lambda f: f[0])]
        result.passed = not result.violations
        return result


def check_frames(code: str) -> FrameCheckResult:
    """Never raises: any internal failure is a pass with a note (fail open)."""
    try:
        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            result = FrameCheckResult(passed=True, notes=["code did not parse; frames not checked"])
        else:
            result = _Checker(tree).run()
    except Exception as exc:
        result = FrameCheckResult(passed=True, notes=[f"frames not checked: {type(exc).__name__}"])
    for note in result.notes:
        logger.debug("frame_check: %s", note)
    return result
