"""Unit tests for the Azure OpenAI TTS wiring in the voiceover pipeline.

These cover the prompt-grounding snippets the generator injects into scenes and
the render-subprocess env that routes manim-voiceover's OpenAIService at Azure.
No network calls — pure config logic.
"""

import importlib

import pytest

from agents.manim_generator import ManimGenerator


@pytest.fixture
def generator():
    return ManimGenerator()


def test_openai_snippet_uses_configured_voice_and_model(generator):
    snippet = generator._get_tts_setup_snippet("openai", "shimmer")
    assert "OpenAIService(" in snippet
    assert 'voice="shimmer"' in snippet
    assert 'model="gpt-4o-mini-tts"' in snippet
    # Bookmark transcription must stay off — it would pull in whisper at render.
    assert "transcription_model=None" in snippet
    # cache_dir must NOT appear: SpeechService keeps an explicit cache_dir as a
    # raw str and its cache lookup does Path division on it -> TypeError on the
    # first narration. Persistence is the runner's job (voiceover-cache symlink).
    assert "cache_dir" not in snippet


def test_gtts_snippet_has_no_cache_dir_either(generator):
    assert "cache_dir" not in generator._get_tts_setup_snippet("gtts", "")


def test_openai_snippet_defaults_voice_when_blank(generator):
    snippet = generator._get_tts_setup_snippet("openai", "")
    assert 'voice="nova"' in snippet


def test_gtts_snippet_is_the_free_fallback(generator):
    snippet = generator._get_tts_setup_snippet("gtts", "")
    assert "GTTSService(" in snippet
    assert "OpenAIService" not in snippet


def test_unknown_service_falls_back_to_gtts(generator):
    assert "GTTSService(" in generator._get_tts_setup_snippet("banana", "")


def test_import_line_matches_service(generator):
    assert generator._get_tts_import("openai") == (
        "from manim_voiceover.services.openai import OpenAIService"
    )
    assert generator._get_tts_import("gtts") == (
        "from manim_voiceover.services.gtts import GTTSService"
    )


def test_render_env_routes_openai_to_azure(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    from rendering import local_runner
    importlib.reload(local_runner)
    env = local_runner._tts_subprocess_env()

    assert env["OPENAI_API_KEY"] == "azure-secret"
    assert env["OPENAI_BASE_URL"] == "https://example.openai.azure.com/openai/v1/"
    # Disambiguates the module-level client so it doesn't refuse Azure+OpenAI env.
    assert env["OPENAI_API_TYPE"] == "openai"


def test_render_env_respects_existing_openai_key(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")

    from rendering import local_runner
    importlib.reload(local_runner)
    env = local_runner._tts_subprocess_env()

    # A real OpenAI key wins — we don't clobber it with the Azure endpoint.
    assert env["OPENAI_API_KEY"] == "real-openai-key"
    assert "OPENAI_BASE_URL" not in env or env["OPENAI_BASE_URL"] != (
        "https://example.openai.azure.com/openai/v1/"
    )


_RENDER_CANARIES = {
    "DATABASE_URL": "postgresql://u:p@db/x",
    "S3_SECRET_KEY": "s3-secret",
    "LANGFUSE_SECRET_KEY": "langfuse-secret",
    "ANTHROPIC_API_KEY": "claude-secret",
    "IP_HASH_SECRET": "hash-secret",
    "TURNSTILE_SECRET_KEY": "turnstile-secret",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=x",
}


def test_render_env_is_an_allowlist(monkeypatch):
    # The render subprocess executes LLM-generated code: app secrets (and the
    # Azure settings themselves) are not inherited; only the derived TTS key is.
    for k, v in _RENDER_CANARIES.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("http_proxy", "http://proxy:3128")

    from rendering import local_runner
    importlib.reload(local_runner)
    env = local_runner._tts_subprocess_env()
    names = {k.upper() for k in env}

    for k in _RENDER_CANARIES:
        assert k not in names, k
    assert not any(n.startswith("AZURE_") for n in names)
    assert "PATH" in names
    assert any(k.lower() == "http_proxy" for k in env)


def test_render_env_preset_key_keeps_its_base_url_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1/")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-secret")

    from rendering import local_runner
    importlib.reload(local_runner)
    env = local_runner._tts_subprocess_env()

    assert env["OPENAI_API_KEY"] == "real-openai-key"
    assert env["OPENAI_BASE_URL"] == "https://proxy.example/v1/"
    # With AZURE_* stripped, the module-level client can't see both and refuse.
    assert not any(k.upper().startswith("AZURE_") for k in env)


def test_render_env_empty_preset_key_falls_back_to_azure(monkeypatch):
    # OPENAI_API_KEY="" builds a client that then 401s; treat it as unset.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-secret")

    from rendering import local_runner
    importlib.reload(local_runner)
    env = local_runner._tts_subprocess_env()

    assert env["OPENAI_API_KEY"] == "azure-secret"
    assert env["OPENAI_BASE_URL"] == "https://example.openai.azure.com/openai/v1/"


def test_render_env_without_any_tts_key_sets_none(monkeypatch):
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    from rendering import local_runner
    importlib.reload(local_runner)
    env = local_runner._tts_subprocess_env()

    assert not any(k.upper().startswith("OPENAI_") for k in env)


def test_real_child_starts_with_the_render_env(monkeypatch):
    # A real interpreter must start under the allowlist (on Windows it needs
    # SYSTEMROOT etc.) and must not see the canaries.
    import json
    import subprocess
    import sys

    for k, v in _RENDER_CANARIES.items():
        monkeypatch.setenv(k, v)

    from rendering import local_runner
    importlib.reload(local_runner)
    result = subprocess.run(
        [sys.executable, "-c", "import json, os, socket; print(json.dumps(sorted(os.environ)))"],
        capture_output=True, text=True, timeout=60,
        env=local_runner._tts_subprocess_env(), stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    seen = {k.upper() for k in json.loads(result.stdout)}
    for k in _RENDER_CANARIES:
        assert k not in seen, k
