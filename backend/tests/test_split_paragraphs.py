"""Paragraph splitting for visual-aid anchors. The frontend mirrors this rule
(frontend/lib/paragraphs.ts), so a paragraph_index chosen at generation time
points at the same paragraph the reader renders."""

from ingestion.text_normalize import normalize_display_text, split_paragraphs


def test_splits_on_blank_lines_and_strips():
    assert split_paragraphs("First para.\n\n  Second para.  \n\n\nThird.") == [
        "First para.",
        "Second para.",
        "Third.",
    ]


def test_single_newlines_stay_in_one_paragraph():
    text = "- item one\n- item two\n\nAfter the list."
    assert split_paragraphs(text) == ["- item one\n- item two", "After the list."]


def test_display_math_block_with_blank_line_is_not_split():
    text = "Intro.\n\n$$\na = b\n\nc = d\n$$\n\nOutro."
    assert split_paragraphs(text) == ["Intro.", "$$\na = b\n\nc = d\n$$", "Outro."]


def test_code_fence_with_blank_line_is_not_split():
    text = "Intro.\n\n```\nx = 1\n\ny = 2\n```\n\nOutro."
    assert split_paragraphs(text) == ["Intro.", "```\nx = 1\n\ny = 2\n```", "Outro."]


def test_empty_and_whitespace_text():
    assert split_paragraphs("") == []
    assert split_paragraphs(None) == []
    assert split_paragraphs("  \n\n \n") == []


def test_whitespace_only_lines_count_as_blank():
    assert split_paragraphs("A.\n   \nB.") == ["A.", "B."]


def test_stable_under_normalization():
    text = "We use attention.\n\n$$\nQK^T\n$$\n\nThen softmax."
    normalized = normalize_display_text(text)
    assert split_paragraphs(normalized) == split_paragraphs(normalize_display_text(normalized))
    assert len(split_paragraphs(normalized)) == 3


# Cross-language contract: frontend/lib/paragraphs.ts must give the same results.


def test_windows_line_endings():
    assert split_paragraphs("A.\r\n\r\nB.\r\nstill B.") == ["A.", "B.\nstill B."]


def test_display_math_opened_and_closed_on_text_lines():
    text = "Eq: $$\na\n\nb\n$$ done.\n\nNext."
    assert split_paragraphs(text) == ["Eq: $$\na\n\nb\n$$ done.", "Next."]


def test_inline_pairs_do_not_open_a_block():
    assert split_paragraphs("$$a$$ and $$b$$\n\nNext.") == ["$$a$$ and $$b$$", "Next."]


def test_unclosed_block_falls_back_to_blank_line_split():
    assert split_paragraphs("Open $$ x\n\nA\n\nB") == ["Open $$ x", "A", "B"]
    assert split_paragraphs("```\ncode\n\nA") == ["```\ncode", "A"]
