"""The allowlisted env for children that run LLM-generated code (render, dry run)."""

import pytest

from subprocess_env import filter_env, scrubbed_env

# App secrets that exist (or existed) in some deployment's env. None may reach
# a child that runs LLM-generated code.
SECRET_CANARIES = {
    "DATABASE_URL": "postgresql://u:p@db/x",
    "S3_SECRET_KEY": "s3-secret",
    "MODAL_TOKEN_ID": "modal-id",
    "MODAL_TOKEN_SECRET": "modal-secret",
    "TURNSTILE_SECRET_KEY": "turnstile-secret",
    "IP_HASH_SECRET": "hash-secret",
    "POSTHOG_API_KEY": "posthog-key",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=x",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
    "AZURE_OPENAI_API_KEY": "azure-secret",
    "ANTHROPIC_API_KEY": "claude-secret",
    "DEDALUS_API_KEY": "dedalus-secret",
    "LANGFUSE_SECRET_KEY": "langfuse-secret",
    "RENDER_API_SECRET": "render-secret",
    "OPENAI_API_KEY": "openai-key",
}


@pytest.fixture()
def canaries(monkeypatch):
    for k, v in SECRET_CANARIES.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("TEXMFHOME", "/tex")


@pytest.mark.parametrize("name", sorted(SECRET_CANARIES))
def test_secrets_are_not_inherited(canaries, name):
    assert name not in {k.upper() for k in scrubbed_env()}


def test_essentials_are_kept(canaries):
    env = {k.upper(): v for k, v in scrubbed_env().items()}
    assert env["PATH"] == "/usr/bin"
    assert env["TEXMFHOME"] == "/tex"


def test_env_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv("LANG", "C.UTF-8")
    assert scrubbed_env()["LANG"] == "C.UTF-8"
    monkeypatch.delenv("LANG")
    assert "LANG" not in scrubbed_env()


def test_matching_ignores_case_and_keeps_spelling():
    environ = {
        "http_proxy": "http://proxy:3128",
        "Path": "/x",
        "ProgramFiles(x86)": "C:\\PF",
        "Azure_OpenAI_Api_Key": "nope",
    }
    env = filter_env({"PATH", "HTTP_PROXY"}, ("PROGRAMFILES",), environ)
    assert env == {"http_proxy": "http://proxy:3128", "Path": "/x", "ProgramFiles(x86)": "C:\\PF"}


def test_no_prefixes_means_exact_names_only():
    assert filter_env({"PATH"}, (), {"PATH": "/x", "PATHEXT": ".EXE"}) == {"PATH": "/x"}
