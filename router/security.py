from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

from fastapi import Header, HTTPException, Request

from database.client import get_user_id_by_api_key


def _parse_allowed_keys() -> List[Tuple[str, Optional[datetime], Optional[str]]]:
    """Read allowed API keys from env (dev fallback or hybrid mode).

    Supports:
    - API_KEY: single key (no expiry)
    - API_KEYS: comma/semicolon separated items. Each item can be:
        - key
        - key@YYYY-MM-DD (expiry date, UTC midnight)
        - key#USER_ID
        - key@YYYY-MM-DD#USER_ID
    Returns list of (key, expiry, user_id?)
    """
    keys_spec = os.getenv("API_KEYS")
    single = os.getenv("API_KEY")
    items: List[str] = []
    if keys_spec:
        tmp = keys_spec.replace(";", ",").split(",")
        items.extend([s.strip() for s in tmp if s.strip()])
    if single:
        items.append(single.strip())

    out: List[Tuple[str, Optional[datetime], Optional[str]]] = []
    for it in items:
        body = it
        user = None
        if "#" in body:
            body, _, user = body.partition("#")
            user = (user or "").strip() or None
        key = body
        exp: Optional[datetime] = None
        if "@" in body:
            k, _, dt = body.partition("@")
            key = k.strip()
            try:
                exp = datetime.fromisoformat(dt.strip())
            except Exception:
                exp = None
        key = (key or "").strip()
        if key:
            out.append((key, exp, user))
    return out


def _extract_presented_key(request: Request, x_api_key: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    q = request.query_params.get("api_key")
    if q and q.strip():
        return q.strip()
    return None


async def require_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """FastAPI dependency to enforce API key authentication.

    Resolution order:
    1) DB-backed lookup (table `api_key` via Prisma helper). If valid, returns {user_id}.
    2) Env fallback (API_KEYS/API_KEY) for dev/hybrid. Returns {user_id: <env_user>} when provided.
    3) If no keys configured anywhere, allow (dev mode) — else 401/403.
    """
    presented = _extract_presented_key(request, x_api_key)
    if not presented:
        # If auth disabled, allow
        if not (_parse_allowed_keys()):
            return {}
        raise HTTPException(status_code=401, detail="Missing API key. Provide X-API-Key or Bearer token.")

    # 1) DB lookup (preferred)
    try:
        user_id = get_user_id_by_api_key(presented)
    except Exception:
        user_id = None
    if user_id:
        request.state.api_user_id = user_id
        return {"user_id": user_id}

    # 2) Env fallback
    allowed = _parse_allowed_keys()
    if not allowed:
        # No keys configured at all => dev mode allow
        return {}
    now = datetime.utcnow()
    for key, exp, uid in allowed:
        if presented == key and (exp is None or now <= exp):
            if uid:
                request.state.api_user_id = uid
                return {"user_id": uid}
            return {}

    raise HTTPException(status_code=403, detail="Invalid or expired API key.")


__all__ = ["require_api_key"]
