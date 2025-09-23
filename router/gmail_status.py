from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
import json

try:
    from google.auth.transport.requests import Request as GARequest
    from google.auth.transport.requests import AuthorizedSession
except Exception:  # pragma: no cover - optional dependency
    GARequest = None  # type: ignore
    AuthorizedSession = None  # type: ignore

try:
    from google.oauth2.credentials import Credentials
except Exception:  # pragma: no cover
    Credentials = None  # type: ignore

try:
    from googleapiclient.discovery import build as gbuild
except Exception:  # pragma: no cover
    gbuild = None  # type: ignore


router = APIRouter()


def _gmail_creds_dir() -> str:
    base_dir = os.getenv("GMAIL_CREDENTIALS_DIR")
    if base_dir:
        return base_dir
    base_dir = os.getenv("CREDENTIALS_DIR")
    if base_dir:
        return os.path.join(base_dir, "gmail")
    return os.path.join(os.getcwd(), ".credentials", "gmail")


class GmailStatus(BaseModel):
    creds_dir: str
    secrets_path: str
    token_path: str
    redirect_uri: Optional[str]
    client_type: Optional[str]
    secrets_exists: bool
    token_exists: bool
    scopes_configured: List[str]
    scopes_granted: List[str]
    has_modify: bool
    has_send: bool
    service_init_ok: bool
    profile_ok: bool
    error: Optional[str] = None


@router.get("/gmail/status", response_model=GmailStatus)
def gmail_status():
    creds_dir = _gmail_creds_dir()
    # Resolve client secrets path with fallbacks
    secrets_candidates = [
        os.getenv("GMAIL_CLIENT_SECRETS_PATH"),
        os.path.join(creds_dir, "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    ]
    secrets_path = next((p for p in secrets_candidates if p and os.path.exists(p)), None) or os.path.join(
        creds_dir, "credentials.json"
    )
    token_candidates = [
        os.getenv("GMAIL_TOKEN_PATH"),
        os.path.join(creds_dir, "token.json"),
        os.path.join(os.getcwd(), "token.json"),
    ]
    token_path = next((p for p in token_candidates if p and os.path.exists(p)), os.path.join(creds_dir, "token.json"))
    redirect_uri = os.getenv("GMAIL_REDIRECT_URI")
    scopes_cfg = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    ).split(",")

    client_type: Optional[str] = None
    try:
        with open(secrets_path) as f:
            s = json.load(f)
        client_type = "web" if "web" in s else ("installed" if "installed" in s else None)
    except Exception:
        pass

    secrets_exists = os.path.exists(secrets_path)
    token_exists = os.path.exists(token_path)
    scopes_granted: List[str] = []
    has_modify = False
    has_send = False
    service_init_ok = False
    profile_ok = False
    error: Optional[str] = None

    if Credentials and GARequest and token_exists:
        try:
            creds = Credentials.from_authorized_user_file(token_path, scopes=scopes_cfg)
            if not creds.valid and creds.refresh_token:
                creds.refresh(GARequest())
            scopes_granted = list(creds.scopes or [])
            has_modify = any("gmail.modify" in s for s in scopes_granted)
            has_send = any("gmail.send" in s for s in scopes_granted)
            if gbuild:
                try:
                    svc = gbuild("gmail", "v1", credentials=creds, cache_discovery=False)
                    service_init_ok = True
                    try:
                        svc.users().getProfile(userId="me").execute()
                        profile_ok = True
                    except Exception as e:
                        error = f"profile check failed: {e}"
                except Exception as e:
                    # Try REST fallback if discovery build fails
                    error = f"service build failed: {e}"
                    try:
                        if AuthorizedSession is None:
                            raise RuntimeError("google-auth transport not available")
                        authed = AuthorizedSession(creds)
                        timeout = float(os.getenv("GMAIL_HTTP_TIMEOUT", "20"))
                        r = authed.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=timeout)
                        if r.ok:
                            profile_ok = True
                            service_init_ok = True
                            error = None
                        else:
                            error = f"rest profile failed: {r.status_code} {r.text}"
                    except Exception as er:
                        error = f"rest profile exception: {er}"
        except Exception as e:
            error = f"credentials load/refresh failed: {e}"
    else:
        if not Credentials or not GARequest:
            error = "google-auth not available"
        elif not token_exists:
            error = "token.json not found"

    return GmailStatus(
        creds_dir=creds_dir,
        secrets_path=secrets_path,
        token_path=token_path,
        redirect_uri=redirect_uri,
        client_type=client_type,
        secrets_exists=secrets_exists,
        token_exists=token_exists,
        scopes_configured=scopes_cfg,
        scopes_granted=scopes_granted,
        has_modify=has_modify,
        has_send=has_send,
        service_init_ok=service_init_ok,
        profile_ok=profile_ok,
        error=error,
    )


class DrySendRequest(BaseModel):
    to: str
    subject: str
    text: str


class DrySendResponse(BaseModel):
    ok: bool
    reason: Optional[str] = None
    missing_scopes: List[str] = []


@router.post("/gmail/dry_send", response_model=DrySendResponse)
def gmail_dry_send(payload: DrySendRequest):
    creds_dir = _gmail_creds_dir()
    token_candidates = [
        os.getenv("GMAIL_TOKEN_PATH"),
        os.path.join(creds_dir, "token.json"),
        os.path.join(os.getcwd(), "token.json"),
    ]
    token_path = next((p for p in token_candidates if p and os.path.exists(p)), os.path.join(creds_dir, "token.json"))
    scopes_cfg = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    ).split(",")

    if not os.path.exists(token_path):
        raise HTTPException(status_code=400, detail="token.json not found; authorize first")
    if not Credentials or not GARequest or not AuthorizedSession:
        raise HTTPException(status_code=500, detail="google-auth not available")

    try:
        creds = Credentials.from_authorized_user_file(token_path, scopes=scopes_cfg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"credentials load failed: {e}")

    granted = list(creds.scopes or [])
    missing = [s for s in [
        "https://www.googleapis.com/auth/gmail.send",
    ] if not any(s in g for g in granted)]

    if missing:
        return DrySendResponse(ok=False, missing_scopes=missing, reason="Insufficient scopes; re-authorize")
    # no network call, just echo that args look fine
    return DrySendResponse(ok=True)


@router.get("/gmail/dry_send", response_model=DrySendResponse)
def gmail_dry_send_get():
    """Helper for browser testing: instruct to POST with JSON body instead of GET."""
    return DrySendResponse(
        ok=False,
        reason="Use POST with JSON body: {to, subject, text}",
        missing_scopes=[],
    )


class SendRequest(BaseModel):
    to: str
    subject: str
    message: str


class SendResponse(BaseModel):
    ok: bool
    id: Optional[str] = None
    error: Optional[str] = None


@router.post("/gmail/send", response_model=SendResponse)
def gmail_send(payload: SendRequest):
    creds_dir = _gmail_creds_dir()
    token_candidates = [
        os.getenv("GMAIL_TOKEN_PATH"),
        os.path.join(creds_dir, "token.json"),
        os.path.join(os.getcwd(), "token.json"),
    ]
    token_path = next((p for p in token_candidates if p and os.path.exists(p)), os.path.join(creds_dir, "token.json"))
    scopes_cfg = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    ).split(",")

    if not os.path.exists(token_path):
        raise HTTPException(status_code=400, detail="token.json not found; authorize first")
    if not Credentials or not GARequest or not AuthorizedSession:
        raise HTTPException(status_code=500, detail="google-auth not available")

    try:
        creds = Credentials.from_authorized_user_file(token_path, scopes=scopes_cfg)
        if not creds.valid and creds.refresh_token:
            creds.refresh(GARequest())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"credentials load/refresh failed: {e}")

    granted = list(creds.scopes or [])
    if not any("gmail.send" in s for s in granted):
        raise HTTPException(
            status_code=400,
            detail=(
                "Insufficient scopes (gmail.send). Delete token.json, include gmail.send in GMAIL_SCOPES, and re-authorize."
            ),
        )

    try:
        # Build and send MIME message via REST API
        from email.mime.text import MIMEText
        import base64

        msg = MIMEText(payload.message)
        msg["to"] = payload.to
        msg["subject"] = payload.subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        authed = AuthorizedSession(creds)
        timeout = float(os.getenv("GMAIL_HTTP_TIMEOUT", "20"))
        resp = authed.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return SendResponse(ok=True, id=data.get("id"))
    except Exception as exc:
        try:
            err = resp.json().get("error", {})  # type: ignore[name-defined]
            detail = err.get("message")
            code = err.get("code")
        except Exception:
            detail = None
            code = None
        msg = f"send failed ({code})" if code else "send failed"
        if detail:
            msg += f": {detail}"
        else:
            msg += f": {exc}"
        return SendResponse(ok=False, error=msg)


class ReadRequest(BaseModel):
    query: Optional[str] = None
    max_results: int = 5
    mark_as_read: bool = False


class ReadResponse(BaseModel):
    ok: bool
    messages: Optional[List[dict]] = None
    error: Optional[str] = None


@router.post("/gmail/read", response_model=ReadResponse)
def gmail_read(payload: ReadRequest):
    creds_dir = _gmail_creds_dir()
    token_candidates = [
        os.getenv("GMAIL_TOKEN_PATH"),
        os.path.join(creds_dir, "token.json"),
        os.path.join(os.getcwd(), "token.json"),
    ]
    token_path = next((p for p in token_candidates if p and os.path.exists(p)), os.path.join(creds_dir, "token.json"))
    scopes_cfg = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    ).split(",")

    if not os.path.exists(token_path):
        raise HTTPException(status_code=400, detail="token.json not found; authorize first")
    if not Credentials:
        raise HTTPException(status_code=500, detail="google-auth not available")

    try:
        creds = Credentials.from_authorized_user_file(token_path, scopes=scopes_cfg)
        if not creds.valid and creds.refresh_token:
            creds.refresh(GARequest())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"credentials load/refresh failed: {e}")

    granted = list(creds.scopes or [])
    if not any(("gmail.readonly" in s) or ("gmail.modify" in s) for s in granted):
        raise HTTPException(
            status_code=400,
            detail=(
                "Insufficient scopes (gmail.readonly or gmail.modify). Delete token.json, include one of them in GMAIL_SCOPES, and re-authorize."
            ),
        )

    try:
        authed = AuthorizedSession(creds)
        timeout = float(os.getenv("GMAIL_HTTP_TIMEOUT", "20"))
        q = payload.query or "in:inbox is:unread"
        max_results = max(1, min(50, int(payload.max_results or 5)))
        resp = authed.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": q, "maxResults": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("messages", []) or []

        import base64

        def _b64url_decode(s: str) -> str:
            try:
                s = s + "=" * (-len(s) % 4)
                return base64.urlsafe_b64decode(s.encode("utf-8")).decode("utf-8", errors="ignore")
            except Exception:
                return ""

        def _extract_bodies(payload: dict) -> tuple[str | None, str | None]:
            mime = (payload or {}).get("mimeType")
            body = (payload or {}).get("body") or {}
            data = body.get("data")
            text_plain = None
            text_html = None
            if mime == "text/plain" and data:
                text_plain = _b64url_decode(data)
            elif mime == "text/html" and data:
                text_html = _b64url_decode(data)
            parts = (payload or {}).get("parts") or []
            for p in parts:
                tp, th = _extract_bodies(p)
                text_plain = text_plain or tp
                text_html = text_html or th
                if text_plain and text_html:
                    break
            return text_plain, text_html

        out: List[dict] = []
        for m in messages:
            mid = m.get("id")
            if not mid:
                continue
            det = authed.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                params={"format": "full"},
                timeout=timeout,
            )
            det.raise_for_status()
            md = det.json()
            headers = {
                h.get("name", "").lower(): h.get("value")
                for h in (md.get("payload", {}) or {}).get("headers", []) or []
            }
            text_plain, text_html = _extract_bodies(md.get("payload", {}) or {})
            max_chars = int(os.getenv("GMAIL_MAX_BODY_CHARS", "8000"))
            if text_plain and len(text_plain) > max_chars:
                text_plain = text_plain[:max_chars]
            if text_html and len(text_html) > max_chars:
                text_html = text_html[:max_chars]
            out.append(
                {
                    "id": mid,
                    "threadId": md.get("threadId"),
                    "labelIds": md.get("labelIds", []),
                    "subject": headers.get("subject"),
                    "from": headers.get("from"),
                    "date": headers.get("date"),
                    "snippet": md.get("snippet"),
                    "body_text": text_plain,
                    "body_html": text_html,
                }
            )
            if payload.mark_as_read and "UNREAD" in (md.get("labelIds") or []):
                try:
                    authed.post(
                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}/modify",
                        json={"removeLabelIds": ["UNREAD"]},
                        timeout=timeout,
                    )
                except Exception:
                    pass

        return ReadResponse(ok=True, messages=out)
    except Exception as exc:
        try:
            err = resp.json().get("error", {})  # type: ignore[name-defined]
            detail = err.get("message")
            code = err.get("code")
        except Exception:
            detail = None
            code = None
        msg = f"read failed ({code})" if code else "read failed"
        if detail:
            msg += f": {detail}"
        else:
            msg += f": {exc}"
        return ReadResponse(ok=False, error=msg)
