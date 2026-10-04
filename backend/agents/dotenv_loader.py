"""Single entry point for loading ``backend/.env`` at import time.

Tests set ``ARXIVISUAL_SKIP_DOTENV=1`` (tests/conftest.py) so a developer's
local ``.env`` cannot change test behavior. Runtime behavior is unchanged.
"""

import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).parent.parent / ".env"


def load_backend_dotenv(env_path: Path | None = None) -> bool:
    """Load ``env_path`` (default ``backend/.env``) if it exists. Returns True if loaded.

    No-op when ``ARXIVISUAL_SKIP_DOTENV`` is "1" or python-dotenv is missing.
    """
    if os.environ.get("ARXIVISUAL_SKIP_DOTENV") == "1":
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False  # python-dotenv not installed, use system env vars
    path = env_path or DEFAULT_ENV_PATH
    if not path.exists():
        return False
    load_dotenv(path)
    return True
