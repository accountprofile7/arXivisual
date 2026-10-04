"""Render-quality override for visual aids (mocks only, no real manim)."""

import asyncio
from pathlib import Path

import pytest

import rendering
from rendering import local_runner

CODE = "from manim import *\nclass A(Scene):\n    def construct(self):\n        pass\n"


def _capture_manim_flag(monkeypatch, quality):
    """Run the real _run_manim_subprocess with subprocess.run faked."""
    seen = {}

    def fake_run(cmd, **_kw):
        seen["cmd"] = cmd
        media = next(a.split("=", 1)[1] for a in cmd if a.startswith("--media_dir="))
        out = Path(media) / "videos"
        out.mkdir(parents=True)
        (out / "a.mp4").write_bytes(b"mp4")

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(local_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(local_runner, "get_manim_executable", lambda: "manim")
    monkeypatch.setattr(local_runner, "_link_persistent_voiceover_cache", lambda *_a: None)
    assert local_runner._run_manim_subprocess(CODE, "A", quality) == b"mp4"
    return seen["cmd"]


def test_low_quality_reaches_manim_command(monkeypatch):
    assert "-ql" in _capture_manim_flag(monkeypatch, "low_quality")


def test_other_qualities_still_map_to_their_flags(monkeypatch):
    assert "-qm" in _capture_manim_flag(monkeypatch, "medium_quality")
    assert "-qh" in _capture_manim_flag(monkeypatch, "high_quality")


@pytest.mark.parametrize(
    "env, expected",
    [
        (None, "low_quality"),
        ("low", "low_quality"),
        ("medium", "medium_quality"),
        ("high", "high_quality"),
        ("medium_quality", "medium_quality"),
        ("  LOW ", "low_quality"),
        ("garbage", "low_quality"),
    ],
)
def test_aid_quality_resolution(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("VISUAL_AID_RENDER_QUALITY", raising=False)
    else:
        monkeypatch.setenv("VISUAL_AID_RENDER_QUALITY", env)
    assert rendering.visual_aid_render_quality() == expected


def _spy_render(monkeypatch):
    seen = {}

    async def fake_render(code, scene_name, quality="low_quality"):
        seen["quality"] = quality
        return b"video"

    async def fake_save(video_bytes, name):
        return f"/videos/{name}"

    monkeypatch.setattr(rendering, "render_manim", fake_render)
    monkeypatch.setattr(rendering, "save_video", fake_save)
    monkeypatch.setattr(rendering, "_dispatch_visual_qa", lambda *_a: None)
    return seen


def test_aid_kind_uses_aid_quality_even_when_caller_passes_default(monkeypatch):
    monkeypatch.setenv("VISUAL_AID_RENDER_QUALITY", "medium")
    seen = _spy_render(monkeypatch)
    asyncio.run(rendering.process_visualization("v", CODE, quality="low_quality", kind="visual_aid"))
    assert seen["quality"] == "medium_quality"


def test_aid_kind_defaults_to_low(monkeypatch):
    monkeypatch.delenv("VISUAL_AID_RENDER_QUALITY", raising=False)
    seen = _spy_render(monkeypatch)
    asyncio.run(rendering.process_visualization("v", CODE, kind="visual_aid"))
    assert seen["quality"] == "low_quality"


def test_existing_callers_keep_their_quality(monkeypatch):
    monkeypatch.setenv("VISUAL_AID_RENDER_QUALITY", "high")
    seen = _spy_render(monkeypatch)
    asyncio.run(rendering.process_visualization("v", CODE))
    assert seen["quality"] == "low_quality"
    asyncio.run(rendering.process_visualization("v", CODE, quality="high_quality"))
    assert seen["quality"] == "high_quality"
