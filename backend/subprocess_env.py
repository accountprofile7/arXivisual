"""Allowlisted environment for subprocesses that run LLM-generated code.

The Manim render and the dry-run gate execute code an LLM wrote, so their
children inherit only what Python, Manim, LaTeX, ffmpeg and gTTS need to start
and reach the network. Every app secret (DATABASE_URL, AZURE_*, S3_*, MODAL_*,
TURNSTILE_*, ANTHROPIC_*, ...) is not inherited by default; a denylist missed
new secrets as they were added.

This is hygiene, not a sandbox: a child running as the same user can still
read its parent's environment through the OS.
"""

import os
from collections.abc import Iterable, Mapping

# Names are matched case-insensitively (Windows env names are), and the
# original spelling is kept so a POSIX lowercase ``http_proxy`` still works.
_ALLOW = frozenset({
    # OS essentials. Windows needs PATH, SYSTEMROOT and USERPROFILE at minimum
    # (manimpango/pydub read PATH, socket init needs SYSTEMROOT, and Manim's
    # config needs a home directory).
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "USERNAME", "OS", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "LANG", "LANGUAGE", "LD_LIBRARY_PATH",
    # Network: proxies and CA bundles (gTTS reaches Google).
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    # Python I/O behaviour.
    "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED",
})
_ALLOW_PREFIXES = (
    "LC_", "PROGRAMFILES", "PROGRAMW6432", "XDG_",
    # LaTeX (MiKTeX / TeX Live) and fontconfig.
    "TEXMF", "MIKTEX", "FONTCONFIG_",
)


def filter_env(
    allow: Iterable[str],
    prefixes: tuple[str, ...] = (),
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Subset of ``environ`` (default: ``os.environ`` now) whose names are allowed.

    Names, ``allow`` and ``prefixes`` are all compared upper-cased.
    """
    allowed = frozenset(a.upper() for a in allow)
    prefixes = tuple(p.upper() for p in prefixes)
    source = os.environ if environ is None else environ
    return {
        k: v for k, v in source.items()
        if k.upper() in allowed or k.upper().startswith(prefixes)
    }


def scrubbed_env() -> dict[str, str]:
    """Environment for a child that runs LLM-generated code (see module docstring)."""
    return filter_env(_ALLOW, _ALLOW_PREFIXES)
