from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
import logging
from pydantic import BaseModel
from typing import Optional

from .security import require_api_key
from database.client import create_api_key_for_user


router = APIRouter()
logger = logging.getLogger("api_keys")


class GenerateKeyRequest(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    label: Optional[str] = None
    expires_at: Optional[str] = None  # ISO date or datetime
    ttl_days: Optional[int] = None


class GenerateKeyResponse(BaseModel):
    ok: bool
    user_id: str | None = None
    key: str | None = None
    id: str | None = None
    expires_at: str | None = None
    label: str | None = None


@router.post("/api_keys/generate", response_model=GenerateKeyResponse)
async def generate_api_key(payload: GenerateKeyRequest, api=Depends(require_api_key)):
    # Only allow generating a key for oneself unless you build an admin layer
    try:
        api_uid = str(api.get("user_id")) if isinstance(api, dict) and api.get("user_id") is not None else None
        logger.info(
            "generate_api_key requested label=%r ttl_days=%r expires_at=%r by_user=%r target_user=%r email=%r",
            payload.label,
            payload.ttl_days,
            payload.expires_at,
            api_uid,
            payload.user_id,
            payload.email,
        )
        # If user_id provided, enforce equality with the caller
        if payload.user_id is not None and api_uid is not None and str(payload.user_id) != api_uid:
            raise HTTPException(status_code=403, detail="API key is not authorized to create keys for other users")
    except HTTPException:
        raise
    except Exception:
        logger.exception("generate_api_key: error while validating caller vs target user")

    # If email is provided and user_id is not, ensure user exists and use that id
    uid = payload.user_id
    if uid is None and payload.email:
        from database.client import ensure_user
        try:
            ensured = ensure_user(payload.email)
        except Exception:
            logger.exception("generate_api_key: ensure_user raised for email=%r", payload.email)
            raise HTTPException(status_code=500, detail="Failed to ensure user by email (internal error)")
        if not ensured:
            logger.error("generate_api_key: ensure_user returned None for email=%r", payload.email)
            raise HTTPException(status_code=500, detail="Failed to ensure user by email")
        uid = ensured
        # If caller has a bound user, ensure it matches the ensured id
        try:
            if api_uid is not None and str(uid) != api_uid:
                raise HTTPException(status_code=403, detail="API key is not authorized for this user")
        except HTTPException:
            raise
        except Exception:
            logger.exception("generate_api_key: error while enforcing caller user match")
    if not uid:
        raise HTTPException(status_code=400, detail="Provide user_id or email")

    try:
        res = create_api_key_for_user(uid, label=payload.label, expires_at=payload.expires_at, ttl_days=payload.ttl_days)
    except Exception:
        logger.exception("generate_api_key: create_api_key_for_user raised for user_id=%r", uid)
        raise HTTPException(status_code=500, detail="Failed to generate API key (internal error)")
    if not res or not res.get("ok"):
        logger.error(
            "generate_api_key: create_api_key_for_user returned failure for user_id=%r label=%r ttl_days=%r expires_at=%r",
            uid,
            payload.label,
            payload.ttl_days,
            payload.expires_at,
        )
        raise HTTPException(status_code=500, detail="Failed to generate API key")
    return GenerateKeyResponse(
        ok=True,
        user_id=str(res.get("user_id")) if res.get("user_id") is not None else None,
        key=res.get("plaintext"),
        id=str(res.get("id")) if res.get("id") is not None else None,
        expires_at=str(res.get("expires_at")) if res.get("expires_at") is not None else None,
        label=payload.label,
    )
