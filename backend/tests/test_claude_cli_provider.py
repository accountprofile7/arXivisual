"""claude_cli provider in agents/base.py — hermetic: subprocess is mocked, the real `claude` never runs."""

import asyncio
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from agents import base

ENV_KEYS = (
    "LLM_PROVIDER", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "DEDALUS_API_KEY",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_EFFORT", "ENVIRONMENT",
    "CLAUDE_CLI_CONCURRENCY", "CLAUDE_CLI_TIMEOUT_SECONDS",
)
FAKE_EXE = "/fake/bin/claude"


@pytest.fixture(autouse=True)
def cli_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    monkeypatch.setattr(base.shutil, "which", lambda name: FAKE_EXE if name == "claude" else None)


def _result_json(text="hello", **extra):
    obj = {
        "type": "result", "is_error": False, "result": text,
        "usage": {
            "input_tokens": 1, "output_tokens": 2,
            "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4,
        },
    }
    obj.update(extra)
    return json.dumps(obj).encode()


class FakeProc:
    def __init__(self, stdout=None, stderr=b"", returncode=0, delay=0.0, tracker=None):
        self._stdout = _result_json() if stdout is None else stdout
        self._stderr = stderr
        self.returncode = None
        self._final_rc = returncode
        self._delay = delay
        self._tracker = tracker
        self.stdin_data = None
        self.killed = False

    async def communicate(self, data=None):
        self.stdin_data = data
        if self._tracker is not None:
            self._tracker["running"] += 1
            self._tracker["peak"] = max(self._tracker["peak"], self._tracker["running"])
        try:
            await asyncio.sleep(self._delay)
        finally:
            if self._tracker is not None:
                self._tracker["running"] -= 1
        self.returncode = self._final_rc
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def spawn(monkeypatch):
    """Patch create_subprocess_exec; returns a recorder with .calls and .procs."""
    rec = SimpleNamespace(calls=[], procs=[], factory=FakeProc)

    async def fake_exec(*argv, **kwargs):
        proc = rec.factory()
        rec.calls.append(SimpleNamespace(argv=list(argv), kwargs=kwargs, cwd_existed=os.path.isdir(kwargs["cwd"])))
        rec.procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return rec


# --- detection and refusals -------------------------------------------------


def test_explicit_claude_cli_is_selected():
    assert base.get_provider() == "claude_cli"


def test_never_auto_detected(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER")
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        base.get_provider()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert base.get_provider() == "anthropic"


def test_refused_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "Production")
    with pytest.raises(RuntimeError, match="production"):
        base.get_provider()


def test_refused_when_azure_configured(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "x")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    with pytest.raises(RuntimeError, match="Azure"):
        base.get_provider()


def test_allowed_with_partial_azure(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    assert base.get_provider() == "claude_cli"


def test_refused_when_executable_missing(monkeypatch):
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="`claude` executable"):
        base.get_provider()


def test_unknown_provider_message_lists_claude_cli(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(RuntimeError, match="claude_cli"):
        base.get_provider()


def test_model_name_and_prewarm_noop(monkeypatch):
    assert base.get_model_name() == "claude-sonnet-5-5"
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-x")
    assert base.get_model_name() == "claude-x"
    asyncio.run(base.prewarm_system_cache("system text"))  # no client, no subprocess


# --- argv, stdin, cwd -------------------------------------------------------


def test_argv_and_stdin(spawn):
    out = asyncio.run(
        base.call_llm("the prompt", system_prompt="SYS", system_suffix="+SUFFIX", model="ignored")
    )
    assert out == "hello"
    (call,) = spawn.calls
    argv = call.argv
    assert argv[0] == FAKE_EXE and argv[1] == "-p"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    assert "--no-session-persistence" in argv
    assert "--safe-mode" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert "--bare" not in argv  # --bare would disable subscription (OAuth) auth
    assert "the prompt" not in argv and not any("SYS" in a for a in argv)  # prompts only via stdin
    assert call.kwargs.get("shell") is None
    stdin = spawn.procs[0].stdin_data.decode()
    assert "SYS+SUFFIX" in stdin and stdin.endswith("the prompt")


def test_model_and_effort_from_env(spawn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5")
    monkeypatch.setenv("ANTHROPIC_EFFORT", "high")
    asyncio.run(base.call_llm("p"))
    argv = spawn.calls[0].argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5"
    assert argv[argv.index("--effort") + 1] == "high"
    assert b"<system_instructions>" not in spawn.procs[0].stdin_data


def test_large_system_prompt_not_in_argv(spawn):
    asyncio.run(base.call_llm("p", system_prompt="x" * 60_000))
    assert all(len(a) < 5_000 for a in spawn.calls[0].argv)
    assert len(spawn.procs[0].stdin_data) > 60_000


def test_cwd_is_fresh_temp_dir_and_removed(spawn):
    asyncio.run(base.call_llm("p"))
    cwd = spawn.calls[0].kwargs["cwd"]
    assert spawn.calls[0].cwd_existed
    assert not os.path.exists(cwd)
    assert os.path.realpath(cwd) != os.path.realpath(os.getcwd())


def test_json_mode_appends_instruction(spawn):
    asyncio.run(base.call_llm("give json", json_mode=True))
    assert spawn.procs[0].stdin_data.decode().endswith("give json" + base._ANTHROPIC_JSON_INSTRUCTION)


# --- child environment ------------------------------------------------------


def test_child_env_scrubbed(spawn, monkeypatch):
    for k, v in {
        "ANTHROPIC_API_KEY": "a", "ANTHROPIC_AUTH_TOKEN": "b", "ANTHROPIC_BASE_URL": "c",
        "ANTHROPIC_FOO": "d", "AZURE_OPENAI_API_KEY": "e", "AZURE_X": "f", "DATABASE_URL": "g",
        "S3_ACCESS_KEY": "h", "LANGFUSE_HOST": "i", "RENDER_API_SECRET": "j", "MODAL_TOKEN_ID": "k",
        "TURNSTILE_SECRET_KEY": "l", "DEDALUS_API_KEY": "m",
        "MY_PATH_MARKER": "keep", "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/bin"))
    asyncio.run(base.call_llm("p"))
    env = spawn.calls[0].kwargs["env"]
    for gone in (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_FOO",
        "AZURE_OPENAI_API_KEY", "AZURE_X", "DATABASE_URL", "S3_ACCESS_KEY", "LANGFUSE_HOST",
        "RENDER_API_SECRET", "MODAL_TOKEN_ID", "TURNSTILE_SECRET_KEY", "DEDALUS_API_KEY",
    ):
        assert gone not in env, gone
    assert env["MY_PATH_MARKER"] == "keep"
    assert "PATH" in env or "Path" in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"  # the subscription credential survives


# --- concurrency and timeout ------------------------------------------------


def test_semaphore_caps_concurrency_at_two(spawn):
    tracker = {"running": 0, "peak": 0}
    spawn.factory = lambda: FakeProc(delay=0.05, tracker=tracker)

    async def go():
        return await asyncio.gather(*(base.call_llm(f"p{i}") for i in range(6)))

    assert asyncio.run(go()) == ["hello"] * 6
    assert tracker["peak"] == 2


def test_concurrency_env_override(spawn, monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_CONCURRENCY", "3")
    tracker = {"running": 0, "peak": 0}
    spawn.factory = lambda: FakeProc(delay=0.05, tracker=tracker)

    async def go():
        await asyncio.gather(*(base.call_llm(f"p{i}") for i in range(6)))

    asyncio.run(go())
    assert tracker["peak"] == 3


def test_timeout_kills_process(spawn, monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_TIMEOUT_SECONDS", "0.05")
    spawn.factory = lambda: FakeProc(delay=5)
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(base.call_llm("p"))
    assert spawn.procs[0].killed
    assert not os.path.exists(spawn.calls[0].kwargs["cwd"])


# --- failure handling -------------------------------------------------------


def test_is_error_raises(spawn):
    spawn.factory = lambda: FakeProc(stdout=_result_json("Credit balance too low", is_error=True))
    with pytest.raises(RuntimeError, match="Credit balance too low"):
        asyncio.run(base.call_llm("p"))


def test_nonzero_exit_raises_with_stderr_tail(spawn):
    spawn.factory = lambda: FakeProc(stdout=b"", stderr=b"x" * 2000 + b"boom: not logged in", returncode=1)
    with pytest.raises(RuntimeError, match="boom: not logged in") as ei:
        asyncio.run(base.call_llm("p"))
    assert len(str(ei.value)) < 700


def test_garbage_output_raises(spawn):
    spawn.factory = lambda: FakeProc(stdout=b"not json at all")
    with pytest.raises(RuntimeError, match="unparseable"):
        asyncio.run(base.call_llm("p"))


def test_missing_result_field_raises(spawn):
    spawn.factory = lambda: FakeProc(stdout=b'{"type": "system"}')
    with pytest.raises(RuntimeError, match="no result"):
        asyncio.run(base.call_llm("p"))


def test_array_output_is_handled(spawn):
    arr = json.dumps([{"type": "system"}, json.loads(_result_json("from array"))]).encode()
    spawn.factory = lambda: FakeProc(stdout=arr)
    assert asyncio.run(base.call_llm("p")) == "from array"


def test_logs_usage(spawn, caplog):
    caplog.set_level("INFO", logger="agents.base")
    asyncio.run(base.call_llm("p"))
    text = caplog.text
    assert "Calling claude_cli/claude-sonnet-5-5" in text
    assert "responded in" in text and "in=1 out=2 cache_read=3 cache_write=4" in text


def test_logs_usage_na(spawn, caplog):
    caplog.set_level("INFO", logger="agents.base")
    spawn.factory = lambda: FakeProc(stdout=json.dumps({"result": "ok"}).encode())
    asyncio.run(base.call_llm("p"))
    assert "usage n/a" in caplog.text


# --- sync path --------------------------------------------------------------


def test_sync_path(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        seen["cwd_existed"] = os.path.isdir(kwargs["cwd"])
        return subprocess.CompletedProcess(argv, 0, _result_json("sync ok"), b"")

    monkeypatch.setattr(base.subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    out = base.call_llm_sync("hi", system_prompt="SYS", json_mode=True)
    assert out == "sync ok"
    argv, kw = seen["argv"], seen["kwargs"]
    assert argv[:2] == [FAKE_EXE, "-p"] and "--no-session-persistence" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert not kw.get("shell")
    assert kw["input"].decode().endswith("hi" + base._ANTHROPIC_JSON_INSTRUCTION)
    assert "ANTHROPIC_API_KEY" not in kw["env"]
    assert seen["cwd_existed"] and not os.path.exists(kw["cwd"])


def test_sync_timeout_and_error(monkeypatch):
    def boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(base.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="timed out"):
        base.call_llm_sync("hi")

    monkeypatch.setattr(
        base.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 2, b"", b"bad flag"),
    )
    with pytest.raises(RuntimeError, match="bad flag"):
        base.call_llm_sync("hi")
