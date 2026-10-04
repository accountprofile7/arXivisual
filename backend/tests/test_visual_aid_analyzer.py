"""VisualAidAnalyzer: paragraph-anchored silent aid candidates (LLM mocked, no network)."""

import asyncio

from agents.visual_aid_analyzer import VisualAidAnalyzer, display_paragraphs
from ingestion.text_normalize import normalize_display_text, split_paragraphs
from models.generation import VisualizationType
from models.paper import Section


def _section(content="", summary="", sid="sec_1"):
    return Section(id=sid, title="Method", level=1, content=content, summary=summary)


def _aid(idx=0, name="Attention", viz="data_flow", beats=("a", "b"), **extra):
    return {
        "section_id": "bogus",
        "concept_name": name,
        "concept_description": "desc",
        "visualization_type": viz,
        "paragraph_index": idx,
        "beats": list(beats),
        **extra,
    }


def _run(monkeypatch, section, aids, **kwargs):
    agent = VisualAidAnalyzer()
    prompts = []

    async def fake(prompt, **kw):
        prompts.append(prompt)
        return {"aids": aids}

    monkeypatch.setattr(agent, "_call_llm_json", fake)
    out = asyncio.run(agent.run("Title", "Abstract", section, **kwargs))
    return out, prompts


SEC = _section(content="First para.\n\nSecond para.\n\nThird para.")


def test_display_paragraphs_prefers_summary():
    sec = _section(content="Raw one.\n\nRaw two.", summary="Sum one.\n\nSum two.\n\nSum three.")
    assert display_paragraphs(sec) == ["Sum one.", "Sum two.", "Sum three."]


def test_display_paragraphs_falls_back_to_content_and_normalizes():
    raw = "  Costs $5 and $6 here.\n\nNext.  "
    paras = display_paragraphs(_section(content=raw))
    assert paras == split_paragraphs(normalize_display_text(raw))
    assert len(paras) == 2 and paras[0] != "Costs $5 and $6 here."


def test_empty_section_makes_no_llm_call(monkeypatch):
    out, prompts = _run(monkeypatch, _section(content="  ", summary=""), [_aid()])
    assert out == [] and prompts == []


def test_prompt_has_numbered_paragraphs_and_skip_concepts(monkeypatch):
    _, prompts = _run(monkeypatch, SEC, [], skip_concepts=["Beam Search"])
    assert "[0] First para." in prompts[0]
    assert "[2] Third para." in prompts[0]
    assert "Beam Search" in prompts[0]


def test_bad_indices_dropped(monkeypatch):
    aids = [
        _aid(0, "A one"),
        _aid(3, "B two"),
        _aid(-1, "C three"),
        _aid("x", "D four"),
        _aid(None, "E five"),
        _aid(True, "F six"),
        {k: v for k, v in _aid(1, "G seven").items() if k != "paragraph_index"},
        _aid("2", "H eight"),
    ]
    out, _ = _run(monkeypatch, SEC, aids)
    assert [c.concept_name for c in out] == ["A one"]


def test_section_id_forced_and_bad_type_falls_back(monkeypatch):
    out, _ = _run(monkeypatch, SEC, [_aid(1, viz="hologram")])
    assert out[0].section_id == "sec_1"
    assert out[0].visualization_type == VisualizationType.EQUATION
    assert out[0].paragraph_index == 1


def test_skip_concepts_and_duplicates_dropped(monkeypatch):
    aids = [
        _aid(0, "Beam search decoding"),
        _aid(1, "Residual connections"),
        _aid(2, "Residual Connection"),
        _aid(2, "Positional encoding"),
    ]
    out, _ = _run(monkeypatch, SEC, aids, skip_concepts=["Beam Search Decoding"])
    assert [c.concept_name for c in out] == ["Residual connections", "Positional encoding"]


def test_max_aids_caps_in_order(monkeypatch):
    aids = [_aid(0, "Alpha thing"), _aid(1, "Bravo thing"), _aid(2, "Charlie thing")]
    out, _ = _run(monkeypatch, SEC, aids, max_aids=2)
    assert [c.concept_name for c in out] == ["Alpha thing", "Bravo thing"]


def test_beats_coerced_and_empties_dropped(monkeypatch):
    out, _ = _run(monkeypatch, SEC, [_aid(0, beats=["x", "", "  ", 3, "y"])])
    assert out[0].beats == ["x", "3", "y"]
    out, _ = _run(monkeypatch, SEC, [{**_aid(0), "beats": "not a list"}])
    assert out[0].beats == []


def test_non_dict_entries_and_missing_aids_key_tolerated(monkeypatch):
    out, _ = _run(monkeypatch, SEC, ["junk", None, _aid(0)])
    assert len(out) == 1


def test_llm_text_fields_are_length_capped(monkeypatch):
    long = "x" * 1000
    out, _ = _run(monkeypatch, SEC, [_aid(name=long, beats=(long,), context=long, concept_description=long)])
    (aid,) = out
    assert len(aid.concept_name) == 120
    assert len(aid.concept_description) == 200 and len(aid.context) == 200
    assert [len(b) for b in aid.beats] == [200]


def test_zero_max_aids_makes_no_llm_call(monkeypatch):
    out, prompts = _run(monkeypatch, SEC, [_aid()], max_aids=0)
    assert out == [] and prompts == []
