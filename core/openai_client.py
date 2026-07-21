"""Creates the OpenAI client and resolves the API key from the usual places."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from openai import OpenAI

# Point at the .env next to this project rather than letting dotenv search from
# the working directory -- the app is launched from too many places for that to
# be reliable. override=True so a stale shell variable cannot shadow the file.
DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(DOTENV_PATH, override=True)


class MissingAPIKey(RuntimeError):
    """Raised when no key is available, with instructions the user can act on."""


SETUP_HELP = """\
No OpenAI API key found.

Local development -- copy `.env.example` to `.env` and paste your key:

    OPENAI_API_KEY=sk-...

Or set it for the current PowerShell session:

    $env:OPENAI_API_KEY = "sk-..."

Streamlit Community Cloud -- add OPENAI_API_KEY under Settings -> Secrets.

Keys are created at https://platform.openai.com/api-keys
"""


def clean_key(value: str) -> str:
    """Tolerate a key pasted as a whole .env line, or wrapped in quotes.

    Pasting `OPENAI_API_KEY=sk-...` into a key field otherwise reaches the API
    verbatim and comes back as a confusing 401 about a key you never typed.
    """
    key = value.strip()
    if key.upper().startswith("OPENAI_API_KEY"):
        _, _, key = key.partition("=")
        key = key.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip()
    return key


def describe(value: str | None) -> str:
    """Summarise a candidate key without ever printing the secret itself."""
    if value is None:
        return "absent"
    cleaned = clean_key(value)
    if not cleaned:
        return f"present but empty (raw length {len(value)})"
    return f"found: {cleaned[:8]}...{cleaned[-4:]} (length {len(cleaned)})"


def diagnose(explicit_key: str | None = None) -> str:
    """Report every place a key is looked for, so a failure names its own cause."""
    try:
        import streamlit as st

        secret = st.secrets.get("OPENAI_API_KEY")  # type: ignore[union-attr]
        secret_line = describe(None if secret is None else str(secret))
    except Exception as exc:
        secret_line = f"unavailable ({type(exc).__name__})"

    return "\n".join(
        [
            "Checked, in order:",
            f"  1. sidebar API key box    {describe(explicit_key)}",
            f"  2. Streamlit secrets      {secret_line}",
            f"  3. OPENAI_API_KEY env var {describe(os.environ.get('OPENAI_API_KEY'))}",
            "",
            f".env expected at  {DOTENV_PATH}",
            f".env exists       {DOTENV_PATH.exists()}",
            f"dotenv autodetect {find_dotenv() or '(none found)'}",
            f"working directory {os.getcwd()}",
        ]
    )


def resolve_api_key(explicit_key: str | None = None) -> str:
    """Find a key: an explicitly supplied one, then Streamlit secrets, then env."""
    if explicit_key and clean_key(explicit_key):
        return clean_key(explicit_key)

    # st.secrets raises if no secrets file exists at all, so this stays guarded
    # and optional -- the app must also run as a plain script.
    try:
        import streamlit as st

        secret = st.secrets.get("OPENAI_API_KEY")  # type: ignore[union-attr]
        if secret and clean_key(str(secret)):
            return clean_key(str(secret))
    except Exception:
        pass

    env_key = os.environ.get("OPENAI_API_KEY", "")
    if clean_key(env_key):
        return clean_key(env_key)

    raise MissingAPIKey(f"{SETUP_HELP}\n{diagnose(explicit_key)}")


def build_client(explicit_key: str | None = None) -> OpenAI:
    """Return a configured OpenAI client, raising MissingAPIKey if unconfigured."""
    return OpenAI(api_key=resolve_api_key(explicit_key))


def get_client(explicit_key: str | None = None) -> OpenAI:
    """Cached client for Streamlit; falls back to a plain client outside it."""
    try:
        import streamlit as st
    except ImportError:
        return build_client(explicit_key)

    @st.cache_resource(show_spinner=False)
    def _cached(key_fingerprint: str) -> OpenAI:
        # The fingerprint is only a cache key -- it never carries the secret
        # itself, so the key does not end up in Streamlit's cache display.
        return build_client(explicit_key)

    key = resolve_api_key(explicit_key)
    return _cached(f"{key[:6]}...{key[-4:]}:{len(key)}")
