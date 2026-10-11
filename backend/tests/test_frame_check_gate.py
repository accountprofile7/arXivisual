"""frame_check gate wiring in generate_single_visualization (hermetic fakes)."""

from tests.test_text_budget_gate import BAD as OVER_BUDGET
from tests.test_text_budget_gate import GOOD, _Render, _run, _run_with_render

FRAMED = (
    "from manim import *\nclass Viz(Scene):\n    def construct(self):\n"
    "        s = Square()\n"
    "        frame = SurroundingRectangle(s, buff=0.4)\n"
    "        self.add(s, frame)\n"
)


def test_flag_on_frame_retries_with_frame_feedback(monkeypatch):
    gen, gates = _run(monkeypatch, [FRAMED, GOOD], narration=True)
    assert gen.runs == 2
    assert "FRAMES ARE NOT ALLOWED" in gen.feedback[0]
    assert "line 5: frame = SurroundingRectangle" in gen.feedback[0]
    assert ("frame_check", 0, False) in gates
    assert ("frame_check", 1, True) in gates
    # The text budget passed, so its feedback is not sent.
    assert "TEXT BUDGET" not in gen.feedback[0]


def test_flag_off_gate_never_runs(monkeypatch):
    gen, gates = _run(monkeypatch, [FRAMED], narration=False)
    assert gen.runs == 1
    assert all(g != "frame_check" for g, _, _ in gates)


def test_last_attempt_with_frame_still_runs_downstream_gates(monkeypatch):
    # Soft gate: never a hard stop, so a frame alone cannot ship render-untested code.
    render = _Render(success=True)
    result, gen, gates = _run_with_render(monkeypatch, [FRAMED], render, max_attempts=1)
    assert render.calls == 1
    assert ("frame_check", 0, False) in gates
    assert result is not None and result.manim_code == FRAMED
    assert gen.runs == 1


def test_frame_miss_with_attempts_left_runs_render_then_retries(monkeypatch):
    render = _Render(success=True)
    _, gen, _ = _run_with_render(monkeypatch, [FRAMED, GOOD], render, max_attempts=2)
    assert render.calls == 2
    assert gen.runs == 2
    assert "FRAMES ARE NOT ALLOWED" in gen.feedback[0]


def test_frame_and_render_feedback_are_combined(monkeypatch):
    render = _Render(success=False)
    _, gen, _ = _run_with_render(monkeypatch, [FRAMED, GOOD], render, max_attempts=2)
    assert "FRAMES ARE NOT ALLOWED" in gen.feedback[0]
    assert "boom" in gen.feedback[0]


def test_frame_and_budget_feedback_are_combined(monkeypatch):
    both = OVER_BUDGET.replace(
        '        self.add(Text(', "        self.add(SurroundingRectangle(Dot()))\n        self.add(Text("
    )
    render = _Render(success=True)
    _, gen, _ = _run_with_render(monkeypatch, [both, GOOD], render, max_attempts=2)
    assert gen.runs == 2
    assert "TEXT BUDGET EXCEEDED" in gen.feedback[0]
    assert "FRAMES ARE NOT ALLOWED" in gen.feedback[0]
