"""Helper utilities for working with per-agent Google OAuth tokens.

This module centralizes the logic for resolving an agent's credential file
from the unified token stored in Postgres (list_account table).  The helper
will lazily materialize the DB token into ``.credentials/google/<agent>/`` so
Google client libraries that insist on filesystem tokens continue to work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Tuple

from database.client import get_agent_google_token


def _agent_root_dir() -> Path:
    """Return the root directory where agent-scoped credentials are stored."""

    base = os.getenv("GOOGLE_AGENT_CREDENTIALS_DIR")
    if base:
        return Path(base)
    return Path(os.getcwd()) / ".credentials" / "google"


def ensure_agent_token_file(
    agent_id: Optional[str],
    fallback_path: Optional[str],
    *,
    filename: str = "token.json",
) -> Tuple[Optional[str], bool]:
    """Ensure the agent-specific Google token exists on disk.

    Args:
        agent_id: The agent identifier. When provided the helper attempts to
            load the unified OAuth token for that agent from the database and
            persist it under ``.credentials/google/<agent_id>/<filename>``.
        fallback_path: Absolute path to the legacy/shared token location used
            before agent-scoped credentials were introduced.  When ``agent_id``
            is ``None`` (or when no agent token exists) this path is returned
            if it exists.
        filename: Name of the credential file to create for the agent.  The
            default ``token.json`` matches the unified token written by the new
            OAuth callback.

    Returns:
        A tuple of (path, is_agent_specific). ``path`` is ``None`` when no
        usable credential file exists. ``is_agent_specific`` is ``True`` when
        the returned path belongs to the agent scope.
    """

    # When the caller does not care about agent-specific credentials just
    # return the shared legacy path (if any).
    if not agent_id:
        if fallback_path and os.path.exists(fallback_path):
            return fallback_path, False
        return (fallback_path if fallback_path else None), False

    root = _agent_root_dir() / str(agent_id)
    token_path = root / filename

    # If the token is already on disk, reuse it without a DB round-trip.
    try:
        if token_path.exists():
            return str(token_path), True
    except Exception:
        # Fall back to DB lookup below if pathlib check fails for any reason.
        pass

    # Otherwise try to hydrate from the database.
    token_data = None
    try:
        token_data = get_agent_google_token(str(agent_id))
    except Exception:
        # Database failures should not explode at import time; allow the
        # caller to surface an authorization error instead.
        token_data = None

    if token_data:
        try:
            root.mkdir(parents=True, exist_ok=True)
            token_path.write_text(json.dumps(token_data), encoding="utf-8")
            return str(token_path), True
        except Exception:
            # If we cannot persist the file, we cannot safely proceed with this
            # credential. Fall back to shared path so callers can raise a clear
            # error message.
            pass

    return None, False


__all__ = ["ensure_agent_token_file"]

