"""VISUAL_NARRATION flag: prompts unchanged when off, visual-aid blocks when on."""

import asyncio
from pathlib import Path

import pytest

from agents.manim_generator import ManimGenerator
from agents.visualization_planner import VisualizationPlanner
from models.generation import (
    Scene,
    VisualizationCandidate,
    VisualizationPlan,
    VisualizationType,
)

_PROMPTS = Path(__file__).parent.parent / "prompts"
_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv("VISUAL_NARRATION", raising=False)


def _plan():
    return VisualizationPlan(
        concept_name="Scaled Dot-Product Attention",
        visualization_type=VisualizationType.DATA_FLOW,
        duration_seconds=30,
        scenes=[Scene(order=1, description="beat", duration_seconds=10, transitions="Write", elements=["Text"])],
        narration_points=["p"],
    )


def _generator():
    g = ManimGenerator.__new__(ManimGenerator)
    g.prompt_template = (_PROMPTS / "manim_generator.md").read_text()
    g.silent_prompt_template = (_PROMPTS / "manim_generator_silent.md").read_text()
    g.system_prompt = "stable system"
    g.silent_example = "# silent example"
    g.examples = {VisualizationType.DATA_FLOW: "# plain example"}
    g.voiceover_examples = {VisualizationType.DATA_FLOW: "# voice example"}
    return g


def _narrated(g):
    return g._build_prompt(_plan(), True, "openai", "nova", "friendly_tutor", (30, 45))


def _planner():
    p = VisualizationPlanner.__new__(VisualizationPlanner)
    p.prompt_template = (_PROMPTS / "visualization_planner.md").read_text()
    return p


def _planner_prompt(p):
    candidate = VisualizationCandidate(
        section_id="s1",
        concept_name="Bottleneck Residual Block",
        concept_description="A 1x1, 3x3, 1x1 stack with identity shortcut.",
        visualization_type=VisualizationType.ARCHITECTURE,
        priority=5,
        context="Bottleneck context.",
    )
    return p._build_prompt(
        candidate,
        "The 1x1 layers reduce 256 channels to 64.",
        "Deep Residual Learning\n\nAbstract text.",
    )


# --- flag off: byte-identical -------------------------------------------------


def test_planner_prompt_unchanged_when_off():
    assert _planner_prompt(_planner()) == (_FIXTURES / "visualization_planner_prompt.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_falsy_flag_values_are_off(monkeypatch, value):
    monkeypatch.setenv("VISUAL_NARRATION", value)
    assert _planner_prompt(_planner()) == (_FIXTURES / "visualization_planner_prompt.txt").read_text(encoding="utf-8")
    assert _narrated(_generator()) == (_FIXTURES / "manim_prompt_narrated_voice.txt").read_text(encoding="utf-8")


def test_generator_prompt_unchanged_when_off():
    g = _generator()
    assert _narrated(g) == (_FIXTURES / "manim_prompt_narrated_voice.txt").read_text(encoding="utf-8")
    plain = g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (30, 45))
    assert plain == (_FIXTURES / "manim_prompt_narrated_plain.txt").read_text(encoding="utf-8")


# --- flag on ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_planner_prompt_has_visual_aid_block_when_on(monkeypatch, value):
    monkeypatch.setenv("VISUAL_NARRATION", value)
    p = _planner()
    prompt = _planner_prompt(p)
    assert '"VISUAL AID: "' in prompt
    assert "proportional" in prompt
    # Center stage built from phrase-sized beats; each corner has one job.
    assert "CENTER STAGE" in prompt
    assert "ONE narration phrase of 4-8 words + ONE visible change" in prompt
    for corner in ("TL", "TR", "BL", "BR"):
        assert corner in prompt
    assert "everyday analogy" in prompt
    assert "first 2 phrases (about 4-6 seconds)" in prompt
    # Original content intact, block sits before the output format, template restored.
    assert "## Narration Points Guidance" in prompt
    assert prompt.index("Visual Aid Moments") < prompt.index("## Output Format (JSON only)")
    assert "Visual Aid Moments" not in p.prompt_template


def test_generator_prompt_has_show_dont_label_block_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    g = _generator()
    prompt = _narrated(g)
    assert "Show, Don't Label" in prompt
    assert '"VISUAL AID:"' in prompt
    assert "proportional" in prompt
    assert "3 words or fewer" in prompt
    # Existing narration, spatial and LaTeX rules still present.
    assert "with self.voiceover(text=" in prompt
    assert "run_time=tracker.duration" in prompt
    assert "Spatial Quality Requirements" in prompt
    assert "LaTeX and MathTex Safety" in prompt
    assert prompt.index("Show, Don't Label") < prompt.index("## Output Contract")
    assert "Show, Don't Label" not in g.prompt_template


def test_silent_and_plain_prompts_unaffected_when_on(monkeypatch):
    g = _generator()
    silent_off = g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (5, 10), silent_loop=True)
    plain_off = g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (30, 45))
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    assert g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (5, 10), silent_loop=True) == silent_off
    assert g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (30, 45)) == plain_off


def _retry_prompt(g):
    captured = {}

    async def fake_enrich(plan):
        return ""

    async def fake_call(prompt, system_prompt=None, system_suffix=""):
        captured["prompt"] = prompt
        return "from manim import *\nclass A(Scene):\n    pass\n"

    g._live_docs_suffix = fake_enrich
    g._call_llm = fake_call
    asyncio.run(g.run_with_feedback(plan=_plan(), previous_code="x", error_message="bad"))
    return captured["prompt"]


def test_retry_prompt_keeps_block_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _retry_prompt(_generator())
    assert "Show, Don't Label" in prompt
    assert "Previous Attempt Failed" in prompt
    assert 'Keep every "VISUAL AID:" scene DRAWN' in prompt
    assert "Keep strict narration quality" in prompt


def test_retry_prompt_unchanged_when_off():
    prompt = _retry_prompt(_generator())
    assert "Show, Don't Label" not in prompt
    assert "VISUAL AID" not in prompt


def test_generator_prompt_has_layout_and_contrast_rules_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _narrated(_generator())
    # Narration phrasing matches the narration check's hard rules.
    assert 'never begin a phrase with "Display", "Show"' in prompt
    assert "Every phrase has at least 4 words" in prompt
    # Tags stay clear of outlines and borders.
    assert "Tags never touch lines" in prompt
    # Opening: title alone, cleared before the diagram, diagram below it.
    assert "appears ALONE" in prompt
    assert "BEFORE any diagram element appears and before the TL preview grows" in prompt
    assert "Never pin a title with `.animate.to_edge(UP)`" in prompt
    # Tag contrast.
    assert "high-contrast color" in prompt
    assert "Never use `GREY`" in prompt
    assert "opacity below 0.8" in prompt
    assert "`font_size` of at least 28" in prompt
    assert "buff of 0.3 or more" in prompt
    # Final composition.
    assert "Final composition" in prompt
    assert "simplified block icons at legible size" in prompt
    assert "Do NOT `FadeOut` the center stage" in prompt


def test_layout_and_contrast_rules_absent_when_off():
    assert "appears ALONE" not in _narrated(_generator())


def test_generator_prompt_has_layout_pacing_and_budget_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _narrated(_generator())
    # Layout convention with concrete coordinates.
    assert "14.2 wide x 8 tall" in prompt
    assert "7.0 x 3.8" in prompt
    assert "3.0 x 1.5" in prompt
    assert "`UL`, `UR`, `DL`, `DR`" in prompt
    assert "flush with the four corners of the base safe area" in prompt
    assert "named constants and helpers" in prompt
    assert "never cleared" in prompt.lower() or "It is never cleared" in prompt
    assert "Indicate" in prompt and "Circumscribe" in prompt
    assert "shrinks to a small resting icon" in prompt
    # Pacing.
    assert "4-8 words" in prompt
    assert "Never narrate over a still frame" in prompt
    # Text budget numbers.
    assert "3 words or fewer" in prompt
    assert "15 tag words or fewer" in prompt
    assert "Paragraph" in prompt
    # Vocabulary.
    assert "funnels" in prompt and "no external images" in prompt


def test_few_shot_snippet_passes_code_validator():
    import re

    from agents.code_validator import CodeValidator
    from agents.frame_check import check_frames
    from agents.text_budget import check_text_budget

    block = (_PROMPTS / "visual_narration_generator.md").read_text(encoding="utf-8")
    snippet = re.search(r"```python\n(.*?)```", block, re.S).group(1)
    body = "\n".join("        " + ln for ln in snippet.splitlines())
    code = (
        "from manim import *\nfrom manim_voiceover import VoiceoverScene\n"
        "class Demo(VoiceoverScene):\n    def construct(self):\n" + body + "\n"
    )
    result = CodeValidator().validate(code)
    assert result.is_valid and not result.needs_regeneration, result.issues_found
    assert check_text_budget(result.code).passed
    assert check_frames(result.code).passed


def _snippet():
    import re

    block = (_PROMPTS / "visual_narration_generator.md").read_text(encoding="utf-8")
    return re.search(r"```python\n(.*?)```", block, re.S).group(1)


def _snippet_constants() -> dict:
    import re

    ns: dict = {}
    for line in _snippet().splitlines():
        if re.fullmatch(r"[A-Z_]+(, [A-Z_]+)* = [\d.]+(, [\d.]+)*", line):
            exec(line, ns)  # numeric constant lines from our own prompt
    return ns


def test_snippet_layout_constants_inside_safe_area_and_non_overlapping():
    ns = _snippet_constants()
    safe_x, safe_y = ns["SAFE_X"], ns["SAFE_Y"]
    assert (safe_x, safe_y) == (6.0, 3.5)  # base prompt / SpatialValidator safe area
    cw, ch, w, h = ns["CORNER_W"], ns["CORNER_H"], ns["CENTER_W"], ns["CENTER_H"]
    corner = (safe_x - cw, safe_x, safe_y - ch, safe_y)  # top-right box: x0, x1, y0, y1
    center = (-w / 2, w / 2, -h / 2, h / 2)
    assert center[1] <= safe_x and center[3] <= safe_y
    x_overlap = corner[0] < center[1] and center[0] < corner[1]
    y_overlap = corner[2] < center[3] and center[2] < corner[3]
    assert not (x_overlap and y_overlap)
    # Opposite corners on the same side never meet either.
    assert 2 * cw < 2 * safe_x and 2 * ch < 2 * safe_y


def test_snippet_dot_respects_minimum_size():
    import re

    radii = [float(r) for r in re.findall(r"Dot\(radius=([\d.]+)", _snippet())]
    assert radii and all(2 * r >= 0.25 for r in radii)


def test_generator_block_states_overrides_and_render_defect_rules(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _narrated(_generator())
    assert "overrides the base spatial rules" in prompt
    assert "never FadeOut or clear the center stage" in prompt
    assert "`buff` is at least 0.3 everywhere" in prompt
    assert "counts TAGS only" in prompt
    assert "`Transform` / `ReplacementTransform` its label too" in prompt
    assert "never drawn twice on top of itself" in prompt
    assert "recap lives ONLY in the TL corner" in prompt
    assert "nothing new is added over the center stage except highlights" in prompt


def test_prompts_ban_frames_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    gen = _narrated(_generator())
    assert "No frames (a static check enforces this)" in gen
    assert "Never construct a `SurroundingRectangle`" in gen
    assert "If the plan asks for an outline, frame, dashed box or placeholder, leave it out" in gen
    # The old rule handed out a frame recipe.
    assert "SurroundingRectangle(..., buff=0.4" not in gen
    plan = _planner_prompt(_planner())
    assert "No frames: never plan an outline, frame, dashed box, placeholder" in plan
    assert "The first center-stage beat draws the real input shape itself" in plan
    assert "Never list `SurroundingRectangle` or `DashedVMobject` in `elements`" in plan


def _flush_box(sx, sy, w, h, safe_x, safe_y):
    """(x0, x1, y0, y1) of a w x h box flush with the safe-area corner (sx, sy)."""
    x = sorted((sx * safe_x, sx * (safe_x - w)))
    y = sorted((sy * safe_y, sy * (safe_y - h)))
    return (*x, *y)


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]


def test_zoom_up_insets_fit_and_never_cover_other_corners():
    ns = _snippet_constants()
    safe = (ns["SAFE_X"], ns["SAFE_Y"])
    iw, ih, cw, ch = ns["INSET_W"], ns["INSET_H"], ns["CORNER_W"], ns["CORNER_H"]
    assert (iw, ih) == (4.7, 2.7)
    assert iw > cw and ih > ch  # a zoom-up, not a same-size corner
    assert ns["ICON_W"] <= cw and ns["ICON_H"] <= ch
    corners = [(sx, sy) for sx in (-1, 1) for sy in (-1, 1)]
    for c in corners:
        inset = _flush_box(*c, iw, ih, *safe)
        for other in corners:
            if other == c:
                continue
            assert not _overlap(inset, _flush_box(*other, iw, ih, *safe))
            assert not _overlap(inset, _flush_box(*other, cw, ch, *safe))
    assert round(safe[1] - ih, 6) >= 0.8  # the middle band |y| < 0.8 is never covered


def test_inset_numbers_in_the_prose_match_the_constants(monkeypatch):
    ns = _snippet_constants()
    sx, sy, iw, ih = ns["SAFE_X"], ns["SAFE_Y"], ns["INSET_W"], ns["INSET_H"]
    x_in, y_in = round(sx - iw, 2), round(sy - ih, 2)  # inner edges of an inset
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    gen = _narrated(_generator())
    assert f"inset centers at x = +/-{sx - iw / 2:.2f}, y = +/-{sy - ih / 2:.2f}" in gen
    assert f"insets span |x| in [{x_in}, {sx}], |y| in [{y_in}, {sy}]" in gen
    assert f"no inset covers the middle band |y| < {y_in}" in gen
    # KEEP CLEAR: the inset footprints and the room they leave inside the center stage.
    w, h = ns["CENTER_W"], ns["CENTER_H"]
    assert f"TL footprint x < -{x_in} and y > {y_in}" in gen
    assert f"TR footprint x > {x_in} and y > {y_in}" in gen
    assert f"BL footprint x < -{x_in} and y < -{y_in}" in gen
    assert f"the middle band (|y| < {y_in}, the full {w} width)" in gen
    assert f"the middle column (|x| < {x_in}, y in [-{h / 2}, {h / 2}])" in gen
    assert f"the bottom-right quarter (x in [{x_in}, {w / 2}], y in [-{h / 2}, {y_in}])" in gen
    # A group that pulses 1.2x left of the column must still fit inside the band.
    tallest = f"{2 * y_in / 1.2:.1f}"
    assert f"Left of x = -{x_in}, every part and its tag stays inside -{y_in} < y < {y_in}" in gen
    assert f"so a group that pulses there, tag included, is centered on y = 0 and at most {tallest} tall" in gen
    assert "put taller groups in the middle column or the bottom-right quarter" in gen
    assert "An arrow or arc that leaves the band stays inside the middle column" in gen
    assert 1.2 * float(tallest) <= 2 * y_in


def test_prompts_describe_the_zoom_up_inset_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    gen = _narrated(_generator())
    assert "ZOOM-UP INSETS" in gen
    assert "4.7 x 2.7 inset flush with the same corner" in gen
    assert "GROWS, PLAYS, then SETTLES" in gen
    assert "FadeIn(card, analogy, target_position=corner_anchor(corner), scale=0.35)" in gen
    assert "FadeOut(card, *temporary_parts, target_position=corner_anchor(corner), scale=0.35)" in gen
    assert "Only one inset is open at a time" in gen
    assert "KEEP CLEAR: no inset ever covers any part of the center diagram" in gen
    assert "give the finished analogy `set_z_index(1)` so the card never covers it" in gen
    assert "fill_color=BLACK, fill_opacity=1.0)" in gen
    assert "The one panel allowed is an inset's filled card with `stroke_width=0`" in gen
    assert "A title must be gone before the first inset grows" in gen
    plan = _planner_prompt(_planner())
    assert "grows out of its corner into a large inset (about 4.7 x 2.7 units)" in plan
    assert "Only one inset is open at a time" in plan
    assert "so no inset ever covers any part of it" in plan
    assert "an inset's filled dark card is a background, not a frame" in plan


_STALE = (
    "EXACTLY 3",
    "Recap (BR)",
    "is the final recap",
    "2-5 second",
    "four corner icons",
    "BR corner",
    "Only the ONE active inset",
    "Only the active inset",
    "gather into a tiny summary",
    "0.3 clear of",
    "Add nothing new to the center stage inside the active inset",
    ".animate.to_edge(UP).scale(",
    "below y = 2.5",
)


def test_prompts_assign_the_corner_roles_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    plan = _planner_prompt(_planner())
    # TL previews and recaps the whole concept, BL explains one step, BR stays empty.
    assert "Top-left (TL): ONE everyday analogy for the WHOLE concept" in plan
    assert "It opens the video as a preview inset over the first 2 phrases" in plan
    assert "grows again over the last 2 phrases as the recap" in plan
    assert "Bottom-left (BL): ONE everyday analogy for one step of the mechanism" in plan
    assert "playing across 2-4 consecutive phrases" in plan
    assert "Bottom-right (BR) stays empty: no icon, no inset, no recap" in plan
    assert "Corner analogies are shapes and motion only, with no words" in plan
    assert "EXACTLY one (TL) preview, the first scene" in plan
    assert "EXACTLY one (BL) moment" in plan
    assert "EXACTLY one (TL) recap, the last scene" in plan
    assert "the (TL) preview first and the (TL) recap last" in plan
    assert "The TL preview plays before the center stage exists, so it highlights nothing" in plan
    assert "Target length stays 30-60 seconds" in plan
    gen = _narrated(_generator())
    assert "TL holds ONE analogy for the WHOLE concept" in gen
    assert "BL holds ONE analogy for one step of the mechanism across 2-4 phrases" in gen
    assert "BR stays EMPTY: no icon, no inset, no recap" in gen
    assert "the TL preview 2, BL 2-4, the TL recap 2" in gen
    assert "The TL preview has nothing to pulse" in gen
    assert "TL REGROW for the recap is the settle in reverse" in gen
    assert "never reuse the faded-out ones" in gen
    assert "FadeIn(card2, *new_parts, target_position=corner_anchor(UL), scale=0.35)" in gen
    assert "part.animate.scale(1 / s).move_to(home)" in gen
    assert "so the video ends with the TL icon at rest" in gen
    assert "Corner analogies and TR pictograms carry no tags" in gen
    assert "LaggedStart(*[Indicate(p) for p in parts], lag_ratio=0.3)" in gen
    assert "never one `Indicate` on the whole diagram" in gen
    assert "for at most 1 second, with no voiceover block of its own" in gen
    assert "store `home = part.get_center()` and `s = icon_scale(part)` before the part shrinks" in gen
    assert "place them where the originals started, give the new parts `set_z_index(1)`" in gen
    assert "use `LEFT` or `RIGHT` instead when `DOWN` would put the tag in an inset footprint" in gen
    assert "the TL, TR and BL icons sit at the edges and BR stays empty" in gen
    assert "Grids of cells or dots (`arrange_in_grid`) may pack tighter" in gen
    # The planner's scene contract the marker print and the run checks rely on.
    assert 'MUST start with "VISUAL AID: " and its corner tag, (TL) or (BL)' in plan
    assert "the TL preview and TL recap scenes are shorter: 2 phrases each" in plan
    assert "the TL recap highlights the finished diagram part by part, from input to output" in plan
    assert "draws the real input shape itself, right after the TL preview settles" in plan
    assert "This replaces the base rule that Scene 1 is a short framing/title beat" in plan
    assert "flashes alone for at most 1 second" in plan
    for stale in _STALE:
        assert stale not in plan and stale not in gen, stale


def test_tr_corner_role_when_on(monkeypatch):
    # Variant (a): a tiny pictogram per phrase inside the TR corner box, never an inset.
    ns = _snippet_constants()
    pic_w, pic_h = round(ns["CORNER_W"] - 0.4, 2), round(ns["CORNER_H"] - 0.4, 2)  # 0.2 margin all round
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    plan = _planner_prompt(_planner())
    assert "### 3. TR corner: one tiny pictogram per phrase" in plan
    assert "written inside the beat as a note that starts with TR:" in plan
    assert 'TR never grows into an inset and has no "VISUAL AID: " scene' in plan
    assert "When the TL recap begins, the last pictogram shrinks to the TR resting icon" in plan
    assert "until the TL recap begins, every center-stage beat, including the beats while the BL inset is open" in plan
    assert "Write TR notes without double quote characters, since the plan is JSON" in plan
    gen = _narrated(_generator())
    assert "TR CORNER, one tiny pictogram per phrase" in gen
    assert "draws the plan's TR: note in the TR corner box, in the SAME `self.play` as that phrase's center change" in gen
    assert f"a shapes-only sketch of at most {pic_w} x {pic_h}" in gen
    assert "`VGroup(...).arrange(..., buff=0.3).move_to(corner_anchor(UR))` with no numeric offsets" in gen
    assert "TR keeps changing while the BL inset is open" in gen
    assert "TR never grows a card or an inset" in gen
    assert "pic.animate.scale(icon_scale(pic)).move_to(corner_anchor(UR))" in gen
    assert "every part at least 0.25 across" in gen
    assert "from the first center-stage phrase after the TL preview until the TL recap begins" in gen
    assert "and a TR pictogram when the next one replaces it" in gen


# Assertions spliced into the snippet: they run inside construct() under the dry run.
_AUTHORED_CHECKS = """\
assert np.allclose(card.get_corner(UL), [-SAFE_X, SAFE_Y, 0])
assert card.get_left()[0] <= analogy.get_left()[0] and analogy.get_right()[0] <= card.get_right()[0]
assert card.get_bottom()[1] <= analogy.get_bottom()[1] and analogy.get_top()[1] <= card.get_top()[1]
assert card.get_fill_opacity() == 1.0
# Indicate pulses the slab 1.2x about its center; the pulse stays out of all three inset footprints.
pulse = slab.copy().scale(1.2)
for corner in (UL, UR, DL):
    x0, x1 = sorted((corner[0] * SAFE_X, corner[0] * (SAFE_X - INSET_W)))
    y0, y1 = sorted((corner[1] * SAFE_Y, corner[1] * (SAFE_Y - INSET_H)))
    assert (pulse.get_right()[0] <= x0 or x1 <= pulse.get_left()[0]
            or pulse.get_top()[1] <= y0 or y1 <= pulse.get_bottom()[1]), corner
assert min(m.z_index for m in analogy.get_family()) > card.z_index
"""
# End state after the TL recap: both cards and dot streams gone, the funnel settled again.
_SETTLED_CHECKS = """\
on_screen = self.get_mobject_family_members()
assert not any(m in on_screen for m in (card, dots, card2, dots2))
assert funnel in on_screen and slab in on_screen
assert np.allclose(funnel.get_center(), corner_anchor(UL))
assert funnel.width <= ICON_W + 1e-6 and funnel.height <= ICON_H + 1e-6
# Exactly icon-sized again: the regrow restored the full size before the second settle.
assert np.isclose(funnel.width, ICON_W) or np.isclose(funnel.height, ICON_H)
assert min(m.z_index for m in dots2.get_family()) > card2.z_index
"""


def _snippet_scene(after: str, authored: str = _AUTHORED_CHECKS) -> str:
    # The authored checks run before the first voiceover block, once every part is built.
    head, tail = _snippet().split("with self.voiceover(", 1)
    body = head + authored + "with self.voiceover(" + tail + after
    return (
        "from manim import *\nfrom manim_voiceover import VoiceoverScene\n"
        "from manim_voiceover.services.openai import OpenAIService\n\n"
        "class Demo(VoiceoverScene):\n    def construct(self):\n"
        '        self.set_speech_service(OpenAIService(voice="nova", transcription_model=None))\n'
        + "\n".join("        " + ln for ln in body.splitlines()) + "\n"
    )


def test_snippet_grows_plays_and_settles_under_manim(monkeypatch):
    from agents.render_tester import RenderTester

    monkeypatch.setenv("RENDER_TEST_EXECUTE", "1")
    tester = RenderTester(timeout_seconds=120)
    result = asyncio.run(tester.test_render(_snippet_scene(_SETTLED_CHECKS)))
    assert result.success, f"{result.error_type}: {result.error_message}"
    # Control: the spliced assertions really run (the gate fails open on harness faults).
    broken = asyncio.run(tester.test_render(_snippet_scene("assert card in self.get_mobject_family_members()\n")))
    assert not broken.success and "AssertionError" in broken.error_message
    # Same for the authored checks: the card is not on screen before the preview grows.
    early = _AUTHORED_CHECKS + "assert card in self.get_mobject_family_members()\n"
    broken = asyncio.run(tester.test_render(_snippet_scene("", authored=early)))
    assert not broken.success and "AssertionError" in broken.error_message
