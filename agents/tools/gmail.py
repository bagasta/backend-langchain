"""
Gmail tools for LangChain agents.

- Prefer official LangChain Gmail tools (langchain_google_community) when available.
- Gracefully degrade to direct REST calls if discovery/build fails or deps belum terpasang.
- Exposes: gmail_search_tool, gmail_send_message_tool, build_gmail_oauth_url
"""

from __future__ import annotations

import json
import os
from typing import Optional

# NOTE: Prefer the modern LangChain tool types
try:
    from langchain_core.tools import Tool as CoreTool, StructuredTool  # type: ignore
except Exception:  # pragma: no cover
    # Fallback for older installs
    from langchain.agents import Tool as CoreTool  # type: ignore
    StructuredTool = None  # type: ignore

# Pydantic v1/v2 compatible import
try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field  # type: ignore

from urllib.parse import urlencode


# -----------------------------
# Paths & scopes (configurable)
# -----------------------------
def _default_gmail_dir() -> str:
    base_dir = os.getenv("GMAIL_CREDENTIALS_DIR") or os.getenv("CREDENTIALS_DIR")
    if base_dir:
        # if CREDENTIALS_DIR is provided, put gmail creds under a gmail/ subdir
        if base_dir == os.getenv("CREDENTIALS_DIR"):
            return os.path.join(base_dir, "gmail")
        return base_dir
    return os.path.join(os.getcwd(), ".credentials", "gmail")


CREDS_DIR = _default_gmail_dir()
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH") or os.path.join(CREDS_DIR, "token.json")

# Resolve client secrets path with sensible fallbacks:
# 1) GMAIL_CLIENT_SECRETS_PATH
# 2) <CREDS_DIR>/credentials.json
# 3) GOOGLE_APPLICATION_CREDENTIALS (commonly used by other Google libs)
_candidate_secrets = [
    os.getenv("GMAIL_CLIENT_SECRETS_PATH"),
    os.path.join(CREDS_DIR, "credentials.json"),
    os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
]
CLIENT_SECRETS_PATH = next(
    (p for p in _candidate_secrets if p and os.path.exists(p)),
    _candidate_secrets[1],  # default to CREDS_DIR/credentials.json
)

# Default scopes: search + send (safe) — Toolkit uses mail.google.com, but we keep it granular
SCOPES = [
    s.strip()
    for s in os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    ).split(",")
    if s.strip()
]

# -----------------------------
# Try official LangChain tools
# -----------------------------
gmail_search_tool = None
gmail_send_message_tool = None
_build_errors: list[str] = []
_err_msg_unavailable: Optional[str] = None

try:
    # Prefer the new package name first
    try:
        from langchain_google_community.gmail.utils import (  # type: ignore
            get_gmail_credentials,
            build_resource_service,
        )
        from langchain_google_community.gmail.search import GmailSearch  # type: ignore
        from langchain_google_community.gmail.send_message import (  # type: ignore
            GmailSendMessage,
        )
    except Exception as e1:  # pragma: no cover
        # Backward compatibility with older installs
        from langchain_community.tools.gmail.utils import (  # type: ignore
            get_gmail_credentials,
            build_resource_service,
        )
        from langchain_community.tools.gmail.search import GmailSearch  # type: ignore
        from langchain_community.tools.gmail.send_message import (  # type: ignore
            GmailSendMessage,
        )

    # Ensure files exist; give helpful error if tidak ada
    missing = []
    if not os.path.exists(CLIENT_SECRETS_PATH):
        missing.append(f"client secrets at {CLIENT_SECRETS_PATH}")
    if not os.path.exists(TOKEN_PATH):
        missing.append(f"token at {TOKEN_PATH}")
    if missing:
        raise ValueError(
            "Missing Gmail OAuth files: "
            + ", ".join(missing)
            + ". Set GMAIL_CLIENT_SECRETS_PATH / GMAIL_TOKEN_PATH atau letakkan file di "
            f"{CREDS_DIR} dan lakukan proses OAuth. Anda juga dapat menyetel GOOGLE_APPLICATION_CREDENTIALS untuk "
            "mengarah ke credentials.json."
        )

    # Build credentials & service
    creds = get_gmail_credentials(
        token_file=TOKEN_PATH,
        client_secrets_file=CLIENT_SECRETS_PATH,
        scopes=SCOPES,
    )

    service = None
    try:
        service = build_resource_service(credentials=creds)  # :contentReference[oaicite:1]{index=1}
    except Exception as be:  # pragma: no cover
        _build_errors.append(str(be))
        try:
            # Discovery fallback (works if googleapiclient is installed)
            from googleapiclient.discovery import build as gbuild

            service = gbuild("gmail", "v1", credentials=creds, cache_discovery=False)
        except Exception as fe:  # pragma: no cover
            _build_errors.append(str(fe))

    if service is not None:
        # Official tool classes implement Runnable Tool API
        _base_search = GmailSearch(api_resource=service)
        _base_send = GmailSendMessage(api_resource=service)

        # Wrap to prevent unhandled exceptions causing 500s
        def _wrap_structured(name: str, base_tool, friendly: str):  # pragma: no cover
            args_schema = getattr(base_tool, "args_schema", None)
            description = getattr(base_tool, "description", None) or f"{friendly} via Gmail API"
            # If StructuredTool is available and base tool exposes an args schema, use it
            if StructuredTool is not None and args_schema is not None:
                def _invoke_with_kwargs(**kwargs):
                    try:
                        out = base_tool.run(**kwargs) if hasattr(base_tool, "run") else base_tool(**kwargs)
                        if isinstance(out, str):
                            return out
                        try:
                            return json.dumps(out, ensure_ascii=False, default=str)
                        except Exception:
                            return str(out)
                    except Exception as exc:
                        return f"{friendly} failed: {exc}"

                return StructuredTool.from_function(
                    name=name,
                    description=description,
                    func=_invoke_with_kwargs,
                    args_schema=args_schema,
                )
            # Fallback to simple Tool (single-input)
            def _func(input: str):
                try:
                    out = base_tool.run(input) if hasattr(base_tool, "run") else base_tool(input)
                    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
                except Exception as exc:
                    return f"{friendly} failed: {exc}"

            return CoreTool(
                name=name,
                func=_func,
                description=description,
            )

        gmail_search_tool = _wrap_structured("gmail_search", _base_search, "Gmail search")
        gmail_send_message_tool = _wrap_structured("gmail_send_message", _base_send, "Gmail send")
        try:  # ensure agent returns immediately after sending
            gmail_send_message_tool.return_direct = True  # type: ignore[attr-defined]
        except Exception:
            pass
    else:
        raise RuntimeError("Failed to build Gmail service: " + " | ".join(_build_errors))

except Exception as outer_e:
    # ----------------------------------------------
    # Manual REST fallback (minimal deps, robust)
    # ----------------------------------------------
    _err_msg_unavailable = str(outer_e)

    def _gmail_stub(*_args, **_kwargs) -> str:
        login_hint = ""
        try:
            url = build_gmail_oauth_url()
            if url:
                login_hint = f" Authorize Gmail here: {url}"
        except Exception:
            pass
        return (
            "Gmail tool unavailable: "
            + _err_msg_unavailable
            + " (check credentials and dependencies)."
            + login_hint
        )

    try:
        import base64
        from email.mime.text import MIMEText
        from google.auth.transport.requests import AuthorizedSession, Request as GARequest
        from google.oauth2.credentials import Credentials as GoogleCredentials
    except Exception as deps_exc:  # pragma: no cover
        # No google-auth either — expose stub tools so Agent tidak error
        gmail_search_tool = CoreTool(
            name="gmail_search",
            description="Search emails in a Gmail account (stub when Gmail deps are missing)",
            func=_gmail_stub,
        )
        gmail_send_message_tool = CoreTool(
            name="gmail_send_message",
            description="Send an email via Gmail (stub when Gmail deps are missing)",
            func=_gmail_stub,
        )
    else:
        # Build creds object if possible, else still serve stubs
        _creds = None
        try:
            if os.path.exists(TOKEN_PATH):
                with open(TOKEN_PATH) as f:
                    token_data = json.load(f)
                _creds = GoogleCredentials.from_authorized_user_info(token_data, SCOPES)
        except Exception:
            pass

        authed = AuthorizedSession(_creds) if _creds else None

        def _ensure_token():  # pragma: no cover
            if _creds and not _creds.valid and getattr(_creds, "refresh_token", None):
                _creds.refresh(GARequest())

        # ---------- Search (REST) ----------
        class GmailSearchArgs(BaseModel):
            query: str = Field(..., description="Valid Gmail search query string")

        def _gmail_search(query: str) -> str:
            if not authed:
                return _gmail_stub()
            try:
                _ensure_token()
                params = {"q": query, "maxResults": 5}
                resp = authed.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                messages = data.get("messages", []) or []
                results = []
                for m in messages[:5]:
                    mid = m.get("id")
                    if not mid:
                        continue
                    det = authed.get(
                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                        params={
                            "format": "metadata",
                            # requests will encode repeated keys for list values:
                            "metadataHeaders": ["Subject", "From", "Date"],
                        },
                    )
                    det.raise_for_status()
                    md = det.json()
                    headers = {
                        h.get("name", "").lower(): h.get("value")
                        for h in (md.get("payload", {}) or {}).get("headers", []) or []
                    }
                    results.append(
                        {
                            "id": mid,
                            "snippet": md.get("snippet"),
                            "subject": headers.get("subject"),
                            "from": headers.get("from"),
                            "date": headers.get("date"),
                        }
                    )
                return json.dumps(results, ensure_ascii=False)
            except Exception as exc:  # pragma: no cover
                try:
                    detail = resp.json().get("error", {}).get("message")  # type: ignore[name-defined]
                except Exception:
                    detail = None
                return f"Gmail search failed: {detail or exc}"

        if StructuredTool is not None:
            gmail_search_tool = StructuredTool.from_function(
                name="gmail_search",
                description=(
                    "Search emails in a Gmail account. Fields: query. Output: JSON list of messages."
                ),
                func=lambda query, **_: _gmail_search(query),
                args_schema=GmailSearchArgs,
            )
        else:  # pragma: no cover - legacy fallback
            gmail_search_tool = CoreTool(
                name="gmail_search",
                description=(
                    "Search emails in a Gmail account. Input: JSON {query: '<gmail query>'}."
                    " Output: JSON list of messages (id, subject, from, date, snippet)."
                ),
                func=lambda s: _gmail_search(s),
            )

        # ---------- Send (REST) ----------
        class GmailSendArgs(BaseModel):
            to: str = Field(..., description="Recipient email address")
            subject: str = Field(..., description="Email subject line")
            message: str = Field(..., description="Email body (plain text or HTML)")
            is_html: bool = Field(
                default=False, description="Set true to send HTML body (Content-Type: text/html)"
            )

        def _gmail_send_message(message: str, to: str, subject: str, is_html: bool = False) -> str:
            if not authed:
                return _gmail_stub()
            try:
                _ensure_token()
                msg = MIMEText(message, _subtype="html" if is_html else "plain", _charset="utf-8")
                msg["to"] = to
                msg["subject"] = subject
                raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
                payload = {"raw": raw}
                resp = authed.post(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json().get("id", "ok")
            except Exception as exc:  # pragma: no cover
                try:
                    err = resp.json().get("error", {})  # type: ignore[name-defined]
                    code = err.get("code")
                    detail = err.get("message")
                except Exception:
                    code, detail = None, None
                if code == 403:
                    return (
                        "Gmail send failed: insufficient permissions. "
                        "Tambahkan scope gmail.send ke GMAIL_SCOPES dan re-authorize (hapus token.json)."
                    )
                return f"Gmail send failed: {detail or exc}"

        if StructuredTool is not None:
            gmail_send_message_tool = StructuredTool.from_function(
                name="gmail_send_message",
                description=(
                    "Send an email via Gmail. Fields: to, subject, message, is_html (optional)."
                ),
                func=lambda to, subject, message, is_html=False, **_: _gmail_send_message(
                    message, to, subject, is_html
                ),
                args_schema=GmailSendArgs,
            )
        else:  # pragma: no cover - legacy fallback
            gmail_send_message_tool = CoreTool(
                name="gmail_send_message",
                description=(
                    "Send an email via Gmail. Provide 'to | subject | message' as a single string."
                ),
                func=lambda s: _gmail_send_message(s, "", ""),
            )
        try:  # ensure direct return
            gmail_send_message_tool.return_direct = True  # type: ignore[attr-defined]
        except Exception:
            pass


# -----------------------------
# OAuth URL helper (for your UI)
# -----------------------------
def build_gmail_oauth_url(state: Optional[str] = None) -> Optional[str]:
    """
    Return an OAuth login URL for Gmail if client_id + redirect_uri are available.
    Reads:
      - GMAIL_CLIENT_SECRETS_PATH (or default under .credentials/gmail/credentials.json)
      - GMAIL_REDIRECT_URI
      - GMAIL_SCOPES
    """
    # Try multiple locations for client secrets to reduce setup friction
    secrets_candidates = [
        os.getenv("GMAIL_CLIENT_SECRETS_PATH"),
        os.path.join(_default_gmail_dir(), "credentials.json"),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    ]
    secrets_path = next((p for p in secrets_candidates if p and os.path.exists(p)), None)
    redirect_uri = os.getenv("GMAIL_REDIRECT_URI")
    scopes = [
        s.strip()
        for s in os.getenv(
            "GMAIL_SCOPES",
            "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
        ).split(",")
        if s.strip()
    ]
    if not secrets_path or not redirect_uri or not os.path.exists(secrets_path):
        return None

    try:
        with open(secrets_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        client_id = (
            data.get("web", {}) or {}
        ).get("client_id") or (data.get("installed", {}) or {}).get("client_id")
        if not client_id:
            return None
    except Exception:
        return None

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


__all__ = ["gmail_search_tool", "gmail_send_message_tool", "build_gmail_oauth_url"]
