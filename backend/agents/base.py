"""Base agent class with provider-switchable LLM support (Azure OpenAI / Claude / Claude CLI / Dedalus)."""

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import weakref
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Load .env file if it exists
from agents.dotenv_loader import load_backend_dotenv  # noqa: E402

load_backend_dotenv()


# Default model per provider. For Azure this is the *deployment name* you
# created in Azure AI Foundry (defaults to the model name, e.g. "gpt-5").
DEFAULT_DEDALUS_MODEL = "claude-sonnet-4-5"


def _azure_deployment() -> str:
    return os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5")


# GPT-5 reasoning tokens count against max_completion_tokens. Agents size
# max_tokens for the *visible* answer, so give the model extra room to think
# or it can return an empty message after exhausting the cap on reasoning.
_AZURE_REASONING_HEADROOM = 4096


def _detect_provider() -> str:
    """Resolve the LLM provider from env: LLM_PROVIDER wins, else auto-detect."""
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit == "azure":
        _require_azure_env()
        return "azure"
    if explicit == "anthropic":
        _require_anthropic_env()
        return "anthropic"
    if explicit == "dedalus":
        _require_dedalus_env()
        return "dedalus"
    if explicit == "claude_cli":
        _require_claude_cli_env()
        return "claude_cli"
    if explicit:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER={explicit!r}. Use 'azure', 'anthropic', 'dedalus' or 'claude_cli'."
        )

    if os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("DEDALUS_API_KEY"):
        return "dedalus"
    raise RuntimeError(
        "No LLM provider configured. Set AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT "
        "(Azure OpenAI, GPT-5 family), ANTHROPIC_API_KEY (Claude) or DEDALUS_API_KEY (Dedalus)."
    )


def _require_azure_env() -> None:
    missing = [
        k for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT")
        if not os.environ.get(k)
    ]
    if missing:
        raise RuntimeError(f"LLM_PROVIDER=azure but missing env vars: {', '.join(missing)}")


def _require_anthropic_env() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")


def _require_dedalus_env() -> None:
    if not os.environ.get("DEDALUS_API_KEY"):
        raise RuntimeError("LLM_PROVIDER=dedalus but DEDALUS_API_KEY is not set.")


def _require_claude_cli_env() -> None:
    """claude_cli is LOCAL-ONLY and opt-in: refuse anywhere it could bill or leak."""
    if os.environ.get("ENVIRONMENT", "").strip().lower() == "production":
        raise RuntimeError(
            "LLM_PROVIDER=claude_cli is local-only (it uses a personal Claude subscription) "
            "and is refused when ENVIRONMENT=production."
        )
    if os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        raise RuntimeError(
            "LLM_PROVIDER=claude_cli refused: Azure OpenAI is fully configured, which looks "
            "like a deployed environment. Unset AZURE_OPENAI_* or use LLM_PROVIDER=azure."
        )
    if not _claude_cli_executable():
        raise RuntimeError(
            "LLM_PROVIDER=claude_cli but the `claude` executable was not found on PATH. "
            "Install Claude Code and log in with your subscription."
        )


def get_provider() -> str:
    """Get the current provider name (validates env on every call)."""
    return _detect_provider()


# ---------------------------------------------------------------------------
# Azure OpenAI (GPT-5 family) — billed against Azure / Microsoft credits
# ---------------------------------------------------------------------------

_azure_async_client = None
_azure_sync_client = None


def _langfuse_enabled() -> bool:
    """Langfuse tracing is on when both project keys are present in the env."""
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def _openai_classes():
    """Return (OpenAI, AsyncOpenAI) classes.

    When Langfuse is configured, return its drop-in wrappers so every LLM call
    is traced automatically (model, token usage, cost, latency). The wrapper
    imports must happen AFTER env vars are loaded — hence the deferred import.
    Falls back to the plain SDK when Langfuse isn't configured (local dev).
    """
    if _langfuse_enabled():
        from langfuse.openai import AsyncOpenAI, OpenAI
    else:
        from openai import AsyncOpenAI, OpenAI
    return OpenAI, AsyncOpenAI


def _azure_base_url() -> str:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    return f"{endpoint}/openai/v1/"


def _get_azure_client():
    global _azure_async_client  # noqa: PLW0603 — lazy singleton cache
    if _azure_async_client is None:
        _, AsyncOpenAI = _openai_classes()
        _azure_async_client = AsyncOpenAI(
            base_url=_azure_base_url(),
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            timeout=300.0,  # 5 min — large paper summarization needs headroom
        )
    return _azure_async_client


def _get_azure_sync_client():
    global _azure_sync_client  # noqa: PLW0603 — lazy singleton cache
    if _azure_sync_client is None:
        OpenAI, _ = _openai_classes()
        _azure_sync_client = OpenAI(
            base_url=_azure_base_url(),
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            timeout=300.0,
        )
    return _azure_sync_client


def _azure_model(model: str | None) -> str:
    """Map a requested model to an Azure deployment name.

    Claude model names (the old Dedalus defaults) map to the configured
    GPT-5 deployment; explicit gpt-* names pass through.
    """
    if not model or model.startswith("claude") or "/" in model:
        return _azure_deployment()
    return model


def _azure_request_kwargs(
    model: str, prompt: str, system_prompt: str, max_tokens: int, json_mode: bool = False
) -> dict:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens + _AZURE_REASONING_HEADROOM,
    }
    if json_mode:
        # The API then guarantees a parseable JSON object — ~4% of papers lost
        # a section's candidates and ~6% a planned visualization to unparseable
        # output (unescaped backslashes, stray quotes) before this.
        kwargs["response_format"] = {"type": "json_object"}
    # minimal | low | medium | high — low keeps the pipeline fast/cheap
    kwargs["reasoning_effort"] = os.environ.get("AZURE_OPENAI_REASONING_EFFORT", "low")
    return kwargs


# ---------------------------------------------------------------------------
# Anthropic (Claude) — official SDK, credentials from ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
_ANTHROPIC_FALLBACKS_BETA = "server-side-fallback-2026-07-01"
# Adaptive thinking counts against max_tokens on Claude; agents size max_tokens
# for the visible answer, so add room to think (cf. _AZURE_REASONING_HEADROOM).
_ANTHROPIC_THINKING_HEADROOM = 8000
_ANTHROPIC_JSON_INSTRUCTION = (
    "\n\nRespond with only a single valid JSON object, no prose, no code fences."
)

_anthropic_async_client = None
_anthropic_sync_client = None


def _anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL


def _anthropic_model_for(model: str | None) -> str:
    """Requested model -> Claude model id. Always the configured model: the
    names callers pass are legacy Dedalus/Azure defaults (the section formatter
    still asks for claude-sonnet-4-5-20250929, which rejects `effort`), so they
    must not override ANTHROPIC_MODEL."""
    del model
    return _anthropic_model()


def _get_anthropic_client():
    global _anthropic_async_client  # noqa: PLW0603 — lazy singleton cache
    if _anthropic_async_client is None:
        import anthropic
        _anthropic_async_client = anthropic.AsyncAnthropic(timeout=300.0)
    return _anthropic_async_client


def _get_anthropic_sync_client():
    global _anthropic_sync_client  # noqa: PLW0603 — lazy singleton cache
    if _anthropic_sync_client is None:
        import anthropic
        _anthropic_sync_client = anthropic.Anthropic(timeout=300.0)
    return _anthropic_sync_client


def _anthropic_system_blocks(system_prompt: str, system_suffix: str = "") -> list[dict]:
    """System text blocks. The breakpoint sits on the LAST block of the stable
    prefix: it and everything before it is cached, the (per-request) suffix
    after it is not."""
    blocks: list[dict] = []
    if system_prompt:
        blocks.append(
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        )
    if system_suffix:
        blocks.append({"type": "text", "text": system_suffix})
    return blocks


def _anthropic_request_kwargs(
    model: str,
    prompt: str,
    system_prompt: str,
    max_tokens: int,
    json_mode: bool = False,
    system_suffix: str = "",
) -> dict:
    if json_mode:
        # Claude has no schema-free JSON mode; call_llm_json's repair+retry backs this up.
        prompt += _ANTHROPIC_JSON_INSTRUCTION
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens + _ANTHROPIC_THINKING_HEADROOM,
        "messages": [{"role": "user", "content": prompt}],
        "betas": [_ANTHROPIC_FALLBACKS_BETA],
        "fallbacks": "default",
        # `thinking` is deliberately omitted (adaptive; Sonnet 5.5 rejects "disabled"),
        # as are temperature/top_p/top_k and assistant prefill.
        "output_config": {"effort": os.environ.get("ANTHROPIC_EFFORT", "medium")},
    }
    if system_prompt or system_suffix:
        # The large Manim reference is re-sent on every generation call: cache it
        # (per-plan text goes in the uncached suffix block after the breakpoint).
        kwargs["system"] = _anthropic_system_blocks(system_prompt, system_suffix)
    return kwargs


def _anthropic_output(msg, model: str, elapsed: float) -> str:
    """Text of a final Anthropic message (skips thinking/fallback blocks)."""
    if msg.stop_reason == "refusal":
        details = getattr(msg, "stop_details", None)
        category = getattr(details, "category", None) or "unspecified"
        raise RuntimeError(f"Claude refused the request (category: {category})")
    if msg.stop_reason == "max_tokens":
        logger.warning(f"[LLM] anthropic/{model} hit max_tokens; output may be truncated")
    output = "".join(
        b.text for b in msg.content if getattr(b, "type", None) == "text"
    )
    u = getattr(msg, "usage", None)
    logger.info(
        f"[LLM] anthropic/{model} responded in {elapsed:.1f}s ({len(output.split())} output words; "
        f"in={getattr(u, 'input_tokens', None)} out={getattr(u, 'output_tokens', None)} "
        f"cache_read={getattr(u, 'cache_read_input_tokens', None)} "
        f"cache_write={getattr(u, 'cache_creation_input_tokens', None)})"
    )
    return output


# ---------------------------------------------------------------------------
# Claude Code CLI (`claude -p`) — LOCAL ONLY, billed to a Claude subscription
# ---------------------------------------------------------------------------

_CLAUDE_CLI_DEFAULT_CONCURRENCY = 2
_CLAUDE_CLI_DEFAULT_TIMEOUT_SECONDS = 600.0
# The CLI has no --system-prompt-file, and Windows command lines cap near 32k chars
# (the Manim system prompt is 17KB+docs), so the real system prompt travels on STDIN
# in a tagged block and --system-prompt only points at it.
_CLAUDE_CLI_SYSTEM_PROMPT = (
    "You are a precise assistant behind an API. If the user message contains a "
    "<system_instructions> block, treat it as your system prompt and follow it exactly. "
    "Reply with only what is asked for. You have no tools."
)
# The child env is an ALLOWLIST (names matched case-insensitively; Windows env names are):
# only what the CLI needs to start, find its login and reach the network. Every app secret
# (ANTHROPIC_*, AZURE_*, DATABASE_URL, S3_*, APPLICATIONINSIGHTS_*, AWS_*, ...) is dropped
# by default, and an ANTHROPIC_API_KEY here would silently switch billing to the API.
_CLAUDE_CLI_ENV_ALLOW = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "USERNAME", "USER", "LOGNAME", "SHELL", "OS", "PROCESSOR_ARCHITECTURE",
    "LANG", "LANGUAGE", "TERM",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
    "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
})
_CLAUDE_CLI_ENV_ALLOW_PREFIXES = ("CLAUDE_CODE_", "LC_", "PROGRAMFILES", "PROGRAMW6432")
# Belt and braces on top of --tools "": deny the built-in tools by name.
_CLAUDE_CLI_DISALLOWED_TOOLS = (
    "Bash,PowerShell,REPL,Read,Write,Edit,MultiEdit,NotebookEdit,Glob,Grep,"
    "WebFetch,WebSearch,Task,Agent,TodoWrite"
)
_CLAUDE_CLI_SYSTEM_TAG_RE = re.compile(r"<(\s*/?\s*system_instructions\s*>)", re.IGNORECASE)
_CLAUDE_CLI_SHIM_EXE_RE = re.compile(r'"%dp0%[\\/]([^"]+?\.exe)"', re.IGNORECASE)

_claude_cli_semaphores: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _is_windows() -> bool:
    return os.name == "nt"


def _claude_cli_executable() -> str | None:
    """Path of the `claude` launcher, preferring the real .exe.

    On Windows `which("claude")` is usually the npm `claude.cmd` shim: killing it on
    timeout would only kill cmd.exe and orphan claude.exe. So prefer a .exe from PATH,
    then the .exe the npm shim points at, and fall back to the shim itself."""
    found = shutil.which("claude")
    if not found or not found.lower().endswith((".cmd", ".bat")):
        return found
    exe = shutil.which("claude.exe")
    if exe:
        return exe
    try:
        text = Path(found).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    m = _CLAUDE_CLI_SHIM_EXE_RE.search(text)
    if m:
        target = Path(found).parent / m.group(1).replace("\\", os.sep)
        if target.is_file():
            return str(target)
    return found


def _claude_cli_kill_tree(pid: int) -> None:
    """Kill the process and its children (Windows: taskkill /T; the child may be a shim)."""
    if not _is_windows():
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, check=False, timeout=15,
        )


def _claude_cli_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL


def _claude_cli_env() -> dict[str, str]:
    """Allowlisted subset of os.environ for the child (see _CLAUDE_CLI_ENV_ALLOW)."""
    return {
        k: v for k, v in os.environ.items()
        if k.upper() in _CLAUDE_CLI_ENV_ALLOW
        or k.upper().startswith(_CLAUDE_CLI_ENV_ALLOW_PREFIXES)
    }


def _claude_cli_timeout() -> float:
    try:
        return float(os.environ.get("CLAUDE_CLI_TIMEOUT_SECONDS", ""))
    except ValueError:
        return _CLAUDE_CLI_DEFAULT_TIMEOUT_SECONDS


def _claude_cli_semaphore():
    """One semaphore per running loop (a single module-level one breaks across asyncio.run calls)."""
    import asyncio

    loop = asyncio.get_running_loop()
    sem = _claude_cli_semaphores.get(loop)
    if sem is None:
        try:
            limit = int(os.environ.get("CLAUDE_CLI_CONCURRENCY", ""))
        except ValueError:
            limit = _CLAUDE_CLI_DEFAULT_CONCURRENCY
        sem = asyncio.Semaphore(max(1, limit))
        _claude_cli_semaphores[loop] = sem
    return sem


def _claude_cli_command(exe: str, model: str) -> list[str]:
    """argv for one headless call (no shell). The prompt is NOT in it; it goes to stdin.

    --bare is deliberately not used: it disables OAuth/keychain, i.e. subscription auth.
    Isolation instead: --safe-mode (no CLAUDE.md, hooks, plugins, skills, MCP), project-only
    setting sources run from an EMPTY temp cwd (so user/project settings contribute nothing),
    --tools "" (no tools), --strict-mcp-config with no --mcp-config (no MCP servers)."""
    return [
        exe, "-p",
        "--output-format", "json",
        "--model", model,
        "--effort", os.environ.get("ANTHROPIC_EFFORT") or "medium",
        "--tools", "",
        "--disallowedTools", _CLAUDE_CLI_DISALLOWED_TOOLS,
        "--strict-mcp-config",
        "--no-session-persistence",
        "--safe-mode",
        "--setting-sources", "project",
        "--system-prompt", _CLAUDE_CLI_SYSTEM_PROMPT,
    ]


def _claude_cli_stdin(prompt: str, system_prompt: str, system_suffix: str, json_mode: bool) -> bytes:
    if json_mode:
        prompt += _ANTHROPIC_JSON_INSTRUCTION
    system = system_prompt + system_suffix
    if system:
        # Untrusted text (a paper) must not be able to close/reopen the system block.
        prompt = _CLAUDE_CLI_SYSTEM_TAG_RE.sub("<\u200b\\1", prompt)
        prompt = f"<system_instructions>\n{system}\n</system_instructions>\n\n{prompt}"
    return prompt.encode("utf-8")


def _claude_cli_tail(data: bytes | str | None, limit: int = 300) -> str:
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    return (data or "").strip()[-limit:]


def _claude_cli_parse(returncode: int | None, stdout: bytes, stderr: bytes) -> tuple[str, dict]:
    """(result text, usage dict) from the CLI's --output-format json; raises RuntimeError."""
    err = _claude_cli_tail(stderr)
    if returncode:
        raise RuntimeError(
            f"claude CLI exited with code {returncode}: {err}"
        )
    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError as e:
        raise RuntimeError(
            f"claude CLI returned unparseable output ({e}); stderr: {err}"
        ) from e
    if isinstance(data, list):  # some versions emit the whole message array
        data = next(
            (d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), None
        )
    if not isinstance(data, dict) or "result" not in data:
        raise RuntimeError(f"claude CLI JSON has no result field; stderr: {err}")
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI reported an error: {_claude_cli_tail(str(data['result']))}; {err}")
    usage = data.get("usage")
    return str(data["result"] or ""), usage if isinstance(usage, dict) else {}


def _claude_cli_log_response(model: str, output: str, usage: dict, elapsed: float) -> None:
    if usage:
        usage_txt = (
            f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
            f"cache_read={usage.get('cache_read_input_tokens')} "
            f"cache_write={usage.get('cache_creation_input_tokens')}"
        )
    else:
        usage_txt = "usage n/a"
    logger.info(
        f"[LLM] claude_cli/{model} responded in {elapsed:.1f}s "
        f"({len(output.split())} output words; {usage_txt})"
    )


def _claude_cli_prepare(prompt: str, system_prompt: str, system_suffix: str, json_mode: bool):
    exe = _claude_cli_executable()
    if not exe:
        raise RuntimeError("claude executable not found on PATH")
    model = _claude_cli_model()
    logger.info(
        f"[LLM] Calling claude_cli/{model} ({len(prompt.split())} input words, "
        f"max_tokens not enforced by the CLI)"
    )
    return (
        model,
        _claude_cli_command(exe, model),
        _claude_cli_stdin(prompt, system_prompt, system_suffix, json_mode),
        _claude_cli_timeout(),
    )


async def _claude_cli_call_async(
    prompt: str, system_prompt: str, system_suffix: str, json_mode: bool
) -> str:
    import asyncio

    model, argv, stdin, timeout = _claude_cli_prepare(prompt, system_prompt, system_suffix, json_mode)
    async with _claude_cli_semaphore():
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="claude_cli_", ignore_cleanup_errors=True) as cwd:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=_claude_cli_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout)
            except TimeoutError:
                raise RuntimeError(f"claude CLI timed out after {timeout:.0f}s (killed)") from None
            finally:
                if proc.returncode is None:
                    _claude_cli_kill_tree(proc.pid)
                    proc.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(proc.wait(), 10)
        output, usage = _claude_cli_parse(proc.returncode, stdout, stderr)
    _claude_cli_log_response(model, output, usage, time.monotonic() - t0)
    return output


def _claude_cli_call_sync(
    prompt: str, system_prompt: str, system_suffix: str, json_mode: bool
) -> str:
    model, argv, stdin, timeout = _claude_cli_prepare(prompt, system_prompt, system_suffix, json_mode)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="claude_cli_", ignore_cleanup_errors=True) as cwd:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, env=_claude_cli_env(),
        )
        try:
            stdout, stderr = proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            _claude_cli_kill_tree(proc.pid)
            proc.kill()
            with contextlib.suppress(Exception):
                proc.communicate(timeout=10)
            raise RuntimeError(f"claude CLI timed out after {timeout:.0f}s (killed)") from None
        except BaseException:
            _claude_cli_kill_tree(proc.pid)
            proc.kill()
            raise
    output, usage = _claude_cli_parse(proc.returncode, stdout, stderr)
    _claude_cli_log_response(model, output, usage, time.monotonic() - t0)
    return output


# ---------------------------------------------------------------------------
# Dedalus (legacy fallback)
# ---------------------------------------------------------------------------

_dedalus_runner = None


def _get_dedalus_runner():
    """Get or create the shared DedalusRunner instance."""
    global _dedalus_runner  # noqa: PLW0603 — lazy singleton cache
    if _dedalus_runner is None:
        from dedalus_labs import AsyncDedalus, DedalusRunner
        client = AsyncDedalus(
            timeout=300.0,  # 5 min — large paper summarization needs headroom
        )
        _dedalus_runner = DedalusRunner(client, verbose=False)
    return _dedalus_runner


def _dedalus_model(model: str) -> str:
    """Convert bare model name to Dedalus format (anthropic/model-name)."""
    if "/" in model:
        return model
    return f"anthropic/{model}"


def _get_client() -> None:
    """Compatibility shim: agents don't hold a direct SDK client."""
    return None


def get_model_name(model: str | None = None) -> str:
    """Get the model name for the active provider."""
    if model:
        return model
    if get_provider() == "azure":
        return _azure_deployment()
    provider = get_provider()
    if provider == "anthropic":
        return _anthropic_model()
    if provider == "claude_cli":
        return _claude_cli_model()
    return DEFAULT_DEDALUS_MODEL


# ---------------------------------------------------------------------------
# Standalone LLM call helpers (usable outside BaseAgent, e.g. validators)
# ---------------------------------------------------------------------------

def _with_trace_name(kwargs: dict, name: str | None) -> dict:
    """Attach a Langfuse generation name — only when tracing is on, since the
    plain OpenAI client rejects the unknown ``name`` kwarg."""
    if name and _langfuse_enabled():
        kwargs["name"] = name
    return kwargs


async def call_llm(
    prompt: str,
    model: str | None = None,
    system_prompt: str = "",
    max_tokens: int = 4096,
    name: str | None = None,
    json_mode: bool = False,
    system_suffix: str = "",
) -> str:
    """Async LLM call routed through the configured provider.

    ``system_suffix`` is appended to ``system_prompt``: Azure/Dedalus see the
    plain concatenation; Anthropic sends it as a second, uncached system block
    so ``system_prompt`` alone is the cached, cross-request prefix."""
    provider = get_provider()
    combined_system = system_prompt + system_suffix
    input_words = len(prompt.split())
    t0 = time.monotonic()

    if provider == "azure":
        resolved = _azure_model(model)
        logger.info(f"[LLM] Calling azure/{resolved} ({input_words} input words, max_tokens={max_tokens})")
        try:
            client = _get_azure_client()
            resp = await client.chat.completions.create(
                **_with_trace_name(
                    _azure_request_kwargs(resolved, prompt, combined_system, max_tokens, json_mode),
                    name,
                )
            )
            output = resp.choices[0].message.content or ""
            elapsed = time.monotonic() - t0
            logger.info(f"[LLM] azure/{resolved} responded in {elapsed:.1f}s ({len(output.split())} output words)")
            return output
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(f"[LLM] azure/{resolved} FAILED after {elapsed:.1f}s: {type(e).__name__}: {e}")
            raise

    if provider == "anthropic":
        resolved = _anthropic_model_for(model)
        logger.info(f"[LLM] Calling anthropic/{resolved} ({input_words} input words, max_tokens={max_tokens})")
        try:
            client = _get_anthropic_client()
            kw = _anthropic_request_kwargs(
                resolved, prompt, system_prompt, max_tokens, json_mode, system_suffix
            )
            # Streaming avoids HTTP timeouts on long generations.
            async with client.beta.messages.stream(**kw) as stream:
                msg = await stream.get_final_message()
            return _anthropic_output(msg, resolved, time.monotonic() - t0)
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(f"[LLM] anthropic/{resolved} FAILED after {elapsed:.1f}s: {type(e).__name__}: {e}")
            raise

    if provider == "claude_cli":
        try:
            return await _claude_cli_call_async(prompt, system_prompt, system_suffix, json_mode)
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(f"[LLM] claude_cli/{_claude_cli_model()} FAILED after {elapsed:.1f}s: {type(e).__name__}: {e}")
            raise

    dedalus_model = _dedalus_model(model or DEFAULT_DEDALUS_MODEL)
    if json_mode:
        logger.warning("[LLM] json_mode requested but the Dedalus provider cannot enforce it; relying on the prompt")
    logger.info(f"[LLM] Calling {dedalus_model} ({input_words} input words, max_tokens={max_tokens})")
    try:
        runner = _get_dedalus_runner()
        result = await runner.run(
            input=prompt,
            model=dedalus_model,
            instructions=combined_system,
            max_tokens=max_tokens,
        )
        elapsed = time.monotonic() - t0
        output = result.final_output or ""
        logger.info(f"[LLM] {dedalus_model} responded in {elapsed:.1f}s ({len(output.split())} output words)")
        return output
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"[LLM] {dedalus_model} FAILED after {elapsed:.1f}s: {type(e).__name__}: {e}")
        raise


def call_llm_sync(
    prompt: str,
    model: str | None = None,
    system_prompt: str = "",
    max_tokens: int = 4096,
    name: str | None = None,
    json_mode: bool = False,
    system_suffix: str = "",
) -> str:
    """Synchronous LLM call routed through the configured provider
    (``system_suffix``: see call_llm)."""
    provider = get_provider()
    combined_system = system_prompt + system_suffix

    if provider == "azure":
        resolved = _azure_model(model)
        client = _get_azure_sync_client()
        resp = client.chat.completions.create(
            **_with_trace_name(
                _azure_request_kwargs(resolved, prompt, combined_system, max_tokens, json_mode),
                name,
            )
        )
        return resp.choices[0].message.content or ""
    if provider == "anthropic":
        resolved = _anthropic_model_for(model)
        t0 = time.monotonic()
        try:
            client = _get_anthropic_sync_client()
            kw = _anthropic_request_kwargs(
                resolved, prompt, system_prompt, max_tokens, json_mode, system_suffix
            )
            with client.beta.messages.stream(**kw) as stream:
                msg = stream.get_final_message()
            return _anthropic_output(msg, resolved, time.monotonic() - t0)
        except Exception as e:
            logger.error(f"[LLM] anthropic/{resolved} FAILED after {time.monotonic() - t0:.1f}s: {type(e).__name__}: {e}")
            raise
    if provider == "claude_cli":
        t0 = time.monotonic()
        try:
            return _claude_cli_call_sync(prompt, system_prompt, system_suffix, json_mode)
        except Exception as e:
            logger.error(f"[LLM] claude_cli/{_claude_cli_model()} FAILED after {time.monotonic() - t0:.1f}s: {type(e).__name__}: {e}")
            raise
    if json_mode:
        logger.warning("[LLM] json_mode requested but the Dedalus provider cannot enforce it; relying on the prompt")

    import asyncio

    runner = _get_dedalus_runner()
    result = asyncio.run(runner.run(
        input=prompt,
        model=_dedalus_model(model or DEFAULT_DEDALUS_MODEL),
        instructions=combined_system,
        max_tokens=max_tokens,
    ))
    return result.final_output or ""


_PREWARM_USER_MESSAGE = "ok"


async def prewarm_system_cache(system_prompt: str, model: str | None = None) -> None:
    """Best-effort Anthropic prompt-cache warm-up for ``system_prompt``.

    One ``max_tokens=0`` request with the same cached system block the real
    calls use, so a burst of concurrent calls reads the cache instead of each
    writing it. No-op on other providers; never raises (warming must not fail
    a job). The cache is model- and prefix-exact."""
    try:
        if get_provider() != "anthropic" or not system_prompt:
            return
        resolved = _anthropic_model_for(model)
        client = _get_anthropic_client()
        msg = await client.beta.messages.create(
            model=resolved,
            max_tokens=0,
            system=_anthropic_system_blocks(system_prompt),
            messages=[{"role": "user", "content": _PREWARM_USER_MESSAGE}],
            betas=[_ANTHROPIC_FALLBACKS_BETA],
        )
        u = getattr(msg, "usage", None)
        logger.info(
            f"[LLM] anthropic/{resolved} cache warm-up "
            f"(in={getattr(u, 'input_tokens', None)} "
            f"cache_read={getattr(u, 'cache_read_input_tokens', None)} "
            f"cache_write={getattr(u, 'cache_creation_input_tokens', None)})"
        )
    except Exception as e:
        logger.warning(f"[LLM] cache warm-up failed (ignored): {type(e).__name__}: {e}")


# An escaped pair is consumed whole (first alternative) so its second
# backslash is never mistaken for a lone one — the old lookahead-only pattern
# turned a correctly escaped \\alpha into \\\alpha and could never salvage a
# compliant reply that also had a trailing comma. \u counts as an escape only
# when four hex digits follow.
_BACKSLASH_RE = re.compile(r'\\\\|\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')
# String literals are matched (and kept) by the first alternative so a comma
# INSIDE a string value ("a, ]") is never mistaken for a trailing comma.
_STRING_OR_TRAILING_COMMA_RE = re.compile(r'("(?:[^"\\]|\\.)*")|,\s*([}\]])')


def repair_json_text(text: str) -> str:
    """Best-effort repair of LLM JSON: escape LONE backslashes (LaTeX inside
    strings: \\alpha -> \\\\alpha; already-escaped pairs untouched) and drop
    trailing commas outside string literals. Idempotent on valid JSON."""
    # Both cases map to an escaped pair: a matched pair stays a pair, a lone
    # backslash becomes one.
    repaired = _BACKSLASH_RE.sub("\\\\\\\\", text)
    return _STRING_OR_TRAILING_COMMA_RE.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), repaired)


_JSON_RETRY_SUFFIX = (
    "\n\nYour previous reply was not valid JSON. Return ONLY a valid JSON object. "
    "Escape every backslash inside strings as \\\\ and every double quote as \\\"."
)


def parse_json_response(content: str) -> dict:
    """JSON from a model reply: fenced or bare, with lenient repair as the
    last resort. Raises ValueError with the reply head on failure."""
    candidates = []
    for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"):
        m = re.search(pattern, content)
        if m:
            candidates.append(m.group(1).strip())
    candidates.append(content.strip())
    for cand in candidates:
        for text in (cand, repair_json_text(cand)):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Failed to parse JSON from response: {content[:500]}")


async def call_llm_json(
    prompt: str,
    *,
    model: str | None = None,
    system_prompt: str = "",
    max_tokens: int = 4096,
    name: str | None = None,
) -> dict:
    """JSON-mode call with one repair retry — the single implementation the
    agents and the ingestion organizer share (two copies had already drifted:
    one retry suffix forgot the double-quote rule)."""
    text = await call_llm(prompt, model=model, system_prompt=system_prompt,
                          max_tokens=max_tokens, name=name, json_mode=True)
    try:
        return parse_json_response(text)
    except ValueError as first:
        logger.warning("%s returned unparseable JSON; retrying once", name or "llm")
        text = await call_llm(prompt + _JSON_RETRY_SUFFIX, model=model, system_prompt=system_prompt,
                              max_tokens=max_tokens, name=name, json_mode=True)
        try:
            return parse_json_response(text)
        except ValueError as second:
            raise second from first


class BaseAgent:
    """
    Base class for all AI agents in the pipeline.

    Provider is selected via env: Azure OpenAI (AZURE_OPENAI_*),
    Claude (ANTHROPIC_API_KEY) or Dedalus (DEDALUS_API_KEY). See _detect_provider().
    """

    def __init__(
        self,
        prompt_file: str,
        model: str | None = None,
        max_tokens: int = 4096,
        system_prompt_file: str = "system/manim_reference.md",
    ):
        self._provider = get_provider()
        self.model = get_model_name(model)
        self.max_tokens = max_tokens
        # Only the code generator needs the 17KB Manim reference; the JSON
        # agents (analyzer, planner) get a short analyst persona instead.
        self.system_prompt = self._load_system_prompt(system_prompt_file)
        self.prompt_template = self._load_prompt(prompt_file)
        # Readable Langfuse generation name, e.g. "manim_generator"
        self._trace_name = Path(prompt_file).stem

        # Keep self.client for any code that still references it directly
        self.client = _get_client()

        # Log active provider
        if self._provider == "azure":
            print(f"☁️  Azure OpenAI → {_azure_model(self.model)}")
        elif self._provider == "anthropic":
            print(f"🧠 Anthropic → {_anthropic_model_for(self.model)}")
        elif self._provider == "claude_cli":
            print(f"🖥️  Claude CLI (subscription, local only) → {_claude_cli_model()}")
        else:
            print(f"🔮 Dedalus SDK → anthropic/{self.model}")

    def _get_prompts_dir(self) -> Path:
        """Get the prompts directory path."""
        return Path(__file__).parent.parent / "prompts"

    def _load_system_prompt(self, relative: str = "system/manim_reference.md") -> str:
        """Load a system prompt file from the prompts directory ('' if absent)."""
        path = self._get_prompts_dir() / relative
        if path.exists():
            return path.read_text()
        return ""

    def _load_prompt(self, filename: str) -> str:
        """Load a prompt template file."""
        path = self._get_prompts_dir() / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text()

    def _format_prompt(self, **kwargs: Any) -> str:
        """
        Format the prompt template with provided variables.

        Uses str.replace() instead of str.format() to avoid issues with
        content containing curly braces (like LaTeX's \\begin{pmatrix}).
        Also handles {{ and }} escape sequences like str.format() does.
        """
        # Mark the TEMPLATE's escaped braces before substitution: doing the
        # unescape afterwards rewrote substituted content too (LaTeX like
        # \\frac{{a}} inside a section became \\frac{a}).
        result = self.prompt_template.replace("{{", "\x00LB\x00").replace("}}", "\x00RB\x00")

        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))

        return result.replace("\x00LB\x00", "{").replace("\x00RB\x00", "}")

    def _parse_json_response(self, content: str) -> dict:
        """See module-level parse_json_response (single implementation)."""
        return parse_json_response(content)

    async def _call_llm_json(self, prompt: str, **kwargs: Any) -> dict:
        """JSON-mode call with one repair retry (see module-level call_llm_json):
        a malformed reply used to drop a section's candidates or a planned
        visualization permanently."""
        return await call_llm_json(
            prompt,
            model=kwargs.get("model", self.model),
            system_prompt=kwargs.get("system_prompt") or self.system_prompt,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            name=self._trace_name,
        )

    def _extract_code_block(self, content: str, language: str = "python") -> str:
        """
        Extract code from a markdown code block.

        Args:
            content: Response content
            language: Language tag to look for

        Returns:
            Extracted code or empty string
        """
        # Try language-specific block first
        pattern = rf"```{language}\s*([\s\S]*?)\s*```"
        match = re.search(pattern, content)
        if match:
            return match.group(1).strip()

        # Try generic code block
        pattern = r"```\s*([\s\S]*?)\s*```"
        match = re.search(pattern, content)
        if match:
            return match.group(1).strip()

        # Return content as-is if no code blocks found
        return content.strip()

    # ------------------------------------------------------------------
    # LLM call helpers — route to the active provider
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        system_suffix: str = "",
    ) -> str:
        """Call the LLM via the configured provider (async)."""
        return await call_llm(
            prompt=prompt,
            model=self.model,
            system_prompt=system_prompt or self.system_prompt,
            max_tokens=max_tokens or self.max_tokens,
            name=self._trace_name,
            json_mode=json_mode,
            system_suffix=system_suffix,
        )

    def _call_llm_sync(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Call the LLM via the configured provider (sync)."""
        return call_llm_sync(
            prompt=prompt,
            model=self.model,
            system_prompt=system_prompt or self.system_prompt,
            max_tokens=max_tokens or self.max_tokens,
            name=self._trace_name,
        )

    # ------------------------------------------------------------------
    # Default run methods
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> dict:
        """
        Run the agent with the given parameters.

        This method should be overridden by subclasses for specific behavior.
        Default implementation formats the prompt and returns parsed JSON.
        """
        prompt = self._format_prompt(**kwargs)
        text = await self._call_llm(prompt)
        return self._parse_json_response(text)

    def run_sync(self, **kwargs: Any) -> dict:
        """Synchronous version of run() for testing."""
        prompt = self._format_prompt(**kwargs)
        text = self._call_llm_sync(prompt)
        return self._parse_json_response(text)
