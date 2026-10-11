"""load_backend_dotenv honours ARXIVISUAL_SKIP_DOTENV (tests never read the real .env)."""

from agents.dotenv_loader import load_backend_dotenv

_KEY = "ARXIVISUAL_TEST_DOTENV_PROBE"


def _env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"{_KEY}=from-file\n", encoding="utf-8")
    return f


def test_skip_flag_prevents_loading(tmp_path, monkeypatch):
    monkeypatch.setenv("ARXIVISUAL_SKIP_DOTENV", "1")
    monkeypatch.setenv(_KEY, "x")
    monkeypatch.delenv(_KEY)  # leaves the key absent; undo restores that state
    assert load_backend_dotenv(_env_file(tmp_path)) is False
    import os

    assert _KEY not in os.environ


def test_loads_without_skip_flag(tmp_path, monkeypatch):
    import os

    monkeypatch.delenv("ARXIVISUAL_SKIP_DOTENV", raising=False)
    monkeypatch.setenv(_KEY, "x")
    monkeypatch.delenv(_KEY)
    try:
        assert load_backend_dotenv(_env_file(tmp_path)) is True
        assert os.environ[_KEY] == "from-file"
    finally:
        os.environ.pop(_KEY, None)


def test_missing_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("ARXIVISUAL_SKIP_DOTENV", raising=False)
    assert load_backend_dotenv(tmp_path / "nope.env") is False
