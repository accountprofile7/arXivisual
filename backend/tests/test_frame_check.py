"""Static frame check for VISUAL_NARRATION videos (pure AST, no LLM)."""

from pathlib import Path

import pytest

from agents.frame_check import check_frames

_EXAMPLES = Path(__file__).parent.parent / "examples"


def _scene(body: str) -> str:
    lines = "\n".join("        " + ln for ln in body.strip("\n").splitlines())
    return "from manim import *\nclass Viz(Scene):\n    def construct(self):\n" + lines + "\n"


# Constructs taken from generated narrated videos (media/test_runs, 2026-10-04).
FRAME_VIA_NAME = """
slab = Rectangle(width=0.5, height=1.6)
frame = SurroundingRectangle(VGroup(slab), color=WHITE, buff=0.4)
dashed = DashedVMobject(frame, num_dashes=40)
dashed.set_stroke(color=WHITE, width=3, opacity=0.5)
self.play(Create(dashed))
"""
DASHED_INLINE_THEN_MOVED = """
outline = DashedVMobject(
    Rectangle(width=5.5, height=2.35, color=WHITE, stroke_width=2)
).move_to(np.array([0.6, -0.425, 0]))
self.play(Create(outline))
"""
DASHED_FLUENT_CHAIN = """
SW, THICK = 0.5, 1.6
out_dotted = DashedVMobject(
    Rectangle(width=SW, height=THICK, color=BLUE).move_to(RIGHT * 3.6)
)
self.add(out_dotted)
"""
RECAP_BOX = """
icons = VGroup(Square(), Circle()).arrange(RIGHT)
box = SurroundingRectangle(icons, buff=0.3, color=WHITE, stroke_width=2)
self.play(Create(box))
"""


@pytest.mark.parametrize("body, expected", [
    (FRAME_VIA_NAME, ["DashedVMobject(frame)", "frame = SurroundingRectangle"]),
    (DASHED_INLINE_THEN_MOVED, ["DashedVMobject(Rectangle("]),
    (DASHED_FLUENT_CHAIN, ["DashedVMobject(Rectangle(width=SW"]),
    (RECAP_BOX, ["box = SurroundingRectangle"]),
])
def test_real_frames_are_flagged(body, expected):
    result = check_frames(_scene(body))
    assert not result.passed
    for needle, violation in zip(expected, result.violations, strict=True):
        assert needle in violation
        assert violation.startswith("line ")


@pytest.mark.parametrize("body", [
    "frame = DashedVMobject(vmobject=Square(side_length=3))",
    "self.play(Create(SurroundingRectangle(Dot())))",
    "frame = SurroundingRectangle(Dot()).set_stroke(opacity=0.4)",
    "self.add(m.DashedVMobject(m.RoundedRectangle(width=4, height=2)))",
    "a = ScreenRectangle(height=3)\nb = a\nself.add(DashedVMobject(b))",
])
def test_frame_variants_are_flagged(body):
    assert not check_frames(_scene(body)).passed


def test_chained_surrounding_rectangle_names_its_variable():
    result = check_frames(_scene("frame = SurroundingRectangle(Dot()).set_stroke(opacity=0.4)"))
    assert result.violations == ["line 4: frame = SurroundingRectangle(...) draws a frame"]


@pytest.mark.parametrize("body", [
    # Highlights, dashed lines, dashed non-boxes, filled panels and plain shapes.
    "s = Square()\nself.play(Circumscribe(s), Indicate(s))",
    "self.play(Create(DashedLine(LEFT, RIGHT)))",
    "self.add(DashedVMobject(Circle(radius=0.3)))",
    "self.add(DashedVMobject(Polygon(LEFT, UP, RIGHT)))",
    "card = RoundedRectangle(width=4.7, height=2.7, fill_color=BLACK, fill_opacity=0.9)\nself.add(card)",
    "self.add(BackgroundRectangle(Dot()))",
    "x = Rectangle(width=2, height=1)\nself.add(x)",
    "frame = Rectangle()\nframe = Circle()\nself.add(DashedVMobject(Circle()))",
])
def test_clean_scenes_pass(body):
    result = check_frames(_scene(body))
    assert result.passed, result.violations


def test_self_referential_names_do_not_loop():
    assert check_frames(_scene("a = b\nb = a\nself.add(DashedVMobject(a))")).passed


def test_unparseable_code_fails_open():
    result = check_frames("def broken(:\n    pass")
    assert result.passed
    assert result.notes


def test_feedback_lists_violations_and_the_fix():
    message = check_frames(_scene(RECAP_BOX)).get_feedback_message()
    assert message.startswith("FRAMES ARE NOT ALLOWED")
    assert "box = SurroundingRectangle" in message
    assert "Indicate, Circumscribe" in message
    assert "real input shape" in message


@pytest.mark.parametrize("name", [
    "voiceover_architecture.py", "voiceover_data_flow.py", "voiceover_equation.py",
])
def test_narrated_few_shot_examples_pass(name):
    # Narrated generation is shown these; they must not teach a banned frame.
    result = check_frames((_EXAMPLES / name).read_text(encoding="utf-8"))
    assert result.passed, result.violations
