"""Static text-budget gate: counting rules."""

from agents.text_budget import MAX_TAG_WORDS, MAX_WORDS_PER_TEXT, check_text_budget


def _scene(body: str) -> str:
    inner = "\n".join("        " + ln for ln in body.strip().splitlines())
    return f"from manim import *\nclass S(Scene):\n    def construct(self):\n{inner}\n"


def test_limits_are_the_spec_values():
    assert MAX_WORDS_PER_TEXT == 3
    assert MAX_TAG_WORDS == 15


def test_compliant_scene_passes():
    r = check_text_budget(_scene('a = Text("256", font_size=28)\nb = Text("bottleneck block")\nc = MathTex("W")'))
    assert r.passed
    assert r.tag_words == 1 + 2 + 1


def test_numeric_and_symbol_strings_count_as_one():
    r = check_text_budget(_scene('Text("1x1")\nText("3×3")\nText("+")\nText("⊕")\nText("256")'))  # noqa: RUF001
    assert r.passed
    assert r.tag_words == 5


def test_mathtex_and_tex_count_one_each():
    r = check_text_budget(_scene('MathTex(r"\\frac{a}{b} + c = d")\nTex("x")'))
    assert r.tag_words == 2


def test_text_over_three_words_is_listed():
    r = check_text_budget(_scene('Text("this is a long sentence")\nText("ok fine")'))
    assert not r.passed
    assert any("this is a long sentence" in v for v in r.violations)
    assert not any("ok fine" in v for v in r.violations)
    assert "keep tags <=3 words" in r.get_feedback_message()
    assert "Replace these with shapes/motion" in r.get_feedback_message()


def test_text_keyword_and_markuptext():
    r = check_text_budget(_scene('Text(text="one two three four")\nMarkupText("one two three four five")'))
    assert len(r.violations) == 2


def test_paragraph_bulleted_title_are_violations():
    r = check_text_budget(_scene('Paragraph("hi")\nBulletedList("a")\nTitle("T")'))
    assert not r.passed
    assert len(r.violations) == 3


def test_total_budget_exceeded():
    lines = "\n".join('Text("alpha beta gamma")' for _ in range(6))  # 18 words, each Text legal
    r = check_text_budget(_scene(lines))
    assert not r.passed
    assert r.tag_words == 18
    assert any("tag words in total" in v for v in r.violations)


def test_exactly_at_budget_passes():
    lines = "\n".join('Text("ab cd ef")' for _ in range(5))
    r = check_text_budget(_scene(lines))
    assert r.tag_words == 15
    assert r.passed


def test_fstring_literal_parts_counted_unknown_vars_noted():
    r = check_text_budget(_scene('n = 4\nText(f"{n} big old heads of attention")\nText(f"{n}")\nlabel = "x y z w"\nText(label)'))
    assert not r.passed
    assert any("heads of attention" in v for v in r.violations) or any("big old" in v for v in r.violations)
    assert len(r.notes) == 3


def test_dynamic_only_is_note_not_violation():
    r = check_text_budget(_scene("Text(name)\nText(f'{k}')"))
    assert r.passed
    assert r.tag_words == 0
    assert len(r.notes) == 2


def test_syntax_error_is_not_this_gates_problem():
    r = check_text_budget("def (:")
    assert r.passed
    assert r.notes
