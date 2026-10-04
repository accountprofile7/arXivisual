"""Anthropic (Claude) provider in agents/base.py — hermetic, client mocked."""

import asyncio
from types import SimpleNamespace

import pytest

from agents import base

ENV_KEYS = (
    "LLM_PROVIDER", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "DEDALUS_API_KEY",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_EFFORT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _msg(blocks=None, stop_reason="end_turn", stop_details=None):
    if blocks is None:
        blocks = [SimpleNamespace(type="text", text="hello")]
    usage = SimpleNamespace(
        input_tokens=1, output_tokens=2, cache_read_input_tokens=3, cache_creation_input_tokens=4
    )
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason, stop_details=stop_details, usage=usage
    )


class _Stream:
    def __init__(self, msg):
        self._msg = msg

    async def get_final_message(self):
        return self._msg


class _AsyncCM:
    def __init__(self, msg):
        self._s = _Stream(msg)

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class _SyncStream:
    def __init__(self, msg):
        self._msg = msg

    def get_final_message(self):
        return self._msg


class _SyncCM:
    def __init__(self, msg):
        self._s = _SyncStream(msg)

    def __enter__(self):
        return self._s

    def __exit__(self, *a):
        return False


class FakeClient:
    def __init__(self, msg, sync=False):
        self.calls = []
        self._msg = msg
        self._sync = sync
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        return _SyncCM(self._msg) if self._sync else _AsyncCM(self._msg)


def _install(monkeypatch, msg=None, sync=False):
    client = FakeClient(msg or _msg(), sync=sync)
    monkeypatch.setattr(base, "_get_anthropic_sync_client" if sync else "_get_anthropic_client", lambda: client)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return client


# --- detection -------------------------------------------------------------

def test_azure_wins_over_anthropic(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "x")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert base.get_provider() == "azure"


def test_anthropic_key_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert base.get_provider() == "anthropic"


def test_anthropic_beats_dedalus(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("DEDALUS_API_KEY", "x")
    assert base.get_provider() == "anthropic"


def test_explicit_anthropic_without_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        base.get_provider()


def test_no_provider_mentions_anthropic():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        base.get_provider()


def test_unknown_provider_lists_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(RuntimeError, match=r"azure.*anthropic.*dedalus"):
        base.get_provider()


def test_model_name_default_and_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert base.get_model_name() == "claude-sonnet-5-5"
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-other")
    assert base.get_model_name() == "claude-other"


# --- request ---------------------------------------------------------------

def test_request_kwargs(monkeypatch):
    client = _install(monkeypatch)
    out = asyncio.run(base.call_llm("hi", system_prompt="SYS", max_tokens=1000))
    assert out == "hello"
    kw = client.calls[0]
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["max_tokens"] == 1000 + base._ANTHROPIC_THINKING_HEADROOM
    assert kw["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "medium"}
    for banned in ("thinking", "temperature", "top_p", "top_k"):
        assert banned not in kw


def test_no_system_omits_key(monkeypatch):
    client = _install(monkeypatch)
    asyncio.run(base.call_llm("hi"))
    assert "system" not in client.calls[0]


def test_model_arg_and_env_effort(monkeypatch):
    client = _install(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_EFFORT", "high")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-env")
    asyncio.run(base.call_llm("hi"))
    asyncio.run(base.call_llm("hi", model="claude-explicit"))
    assert client.calls[0]["model"] == "claude-env"
    assert client.calls[0]["output_config"] == {"effort": "high"}
    assert client.calls[1]["model"] == "claude-explicit"


def test_json_instruction_only_in_json_mode(monkeypatch):
    client = _install(monkeypatch)
    asyncio.run(base.call_llm("hi"))
    asyncio.run(base.call_llm("hi", json_mode=True))
    assert client.calls[0]["messages"][0]["content"] == "hi"
    content = client.calls[1]["messages"][0]["content"]
    assert content.startswith("hi") and "single valid JSON object" in content


def test_text_extraction_skips_non_text(monkeypatch):
    blocks = [
        SimpleNamespace(type="thinking", thinking="hmm"),
        SimpleNamespace(type="text", text="a"),
        SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text="b"),
    ]
    _install(monkeypatch, _msg(blocks))
    assert asyncio.run(base.call_llm("hi")) == "ab"


def test_refusal_raises_with_category(monkeypatch):
    _install(monkeypatch, _msg([], "refusal", SimpleNamespace(category="cyber")))
    with pytest.raises(RuntimeError, match="cyber"):
        asyncio.run(base.call_llm("hi"))


def test_refusal_without_details(monkeypatch):
    _install(monkeypatch, _msg([], "refusal", None))
    with pytest.raises(RuntimeError, match="refused"):
        asyncio.run(base.call_llm("hi"))


def test_max_tokens_warns(monkeypatch, caplog):
    _install(monkeypatch, _msg(stop_reason="max_tokens"))
    with caplog.at_level("WARNING"):
        assert asyncio.run(base.call_llm("hi")) == "hello"
    assert "max_tokens" in caplog.text


def test_sdk_error_propagates(monkeypatch):
    client = _install(monkeypatch)

    def boom(**kw):
        raise ValueError("bad")

    client.beta.messages.stream = boom
    with pytest.raises(ValueError):
        asyncio.run(base.call_llm("hi"))


def test_sync_path(monkeypatch):
    client = _install(monkeypatch, sync=True)
    out = base.call_llm_sync("hi", system_prompt="S", json_mode=True)
    assert out == "hello"
    kw = client.calls[0]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "JSON" in kw["messages"][0]["content"]
