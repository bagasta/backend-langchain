from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import os
import json
from pathlib import Path
from typing import Optional
from database.client import save_agent_google_token

try:
    from google_auth_oauthlib.flow import Flow
except Exception:  # pragma: no cover - optional at runtime
    Flow = None  # type: ignore


router = APIRouter()


class GmailCallbackResponse(BaseModel):
    status: str
    detail: Optional[str] = None
    agent_id: Optional[str] = None


def _gmail_creds_dir() -> str:
    base_dir = os.getenv("GMAIL_CREDENTIALS_DIR")
    if base_dir:
        return base_dir
    base_dir = os.getenv("CREDENTIALS_DIR")
    if base_dir:
        return os.path.join(base_dir, "gmail")
    return os.path.join(os.getcwd(), ".credentials", "gmail")


@router.get("/oauth/gmail/callback", response_model=GmailCallbackResponse)
async def gmail_oauth_callback(request: Request):
    if Flow is None:
        raise HTTPException(status_code=500, detail="google-auth-oauthlib not installed")

    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")  # contains agent_id if provided

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    creds_dir = _gmail_creds_dir()
    # Resolve client secrets path with fallbacks
    secrets_candidates = [
        os.getenv("GMAIL_CLIENT_SECRETS_PATH"),
        os.path.join(creds_dir, "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    ]
    secrets_path = next((p for p in secrets_candidates if p and os.path.exists(p)), None)
    redirect_uri = os.getenv("GMAIL_REDIRECT_URI")
    scopes_env = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    ).split(",")
    # Prefer scopes returned by Google in the callback if present to avoid mismatches
    scopes_cb = params.get("scope")
    if scopes_cb:
        # Google returns scopes space-delimited; split on whitespace
        scopes = [s for s in scopes_cb.split() if s]
    else:
        scopes = scopes_env

    if not secrets_path or not os.path.exists(secrets_path):
        raise HTTPException(status_code=500, detail=f"Missing client secrets at {secrets_path}")
    if not redirect_uri:
        # Fallback to the current request URL without query parameters
        host = request.headers.get("host")
        if not host:
            raise HTTPException(status_code=500, detail="GMAIL_REDIRECT_URI not configured")
        redirect_uri = f"{request.url.scheme}://{host}{request.url.path}"

    # Validate secrets type and redirect compatibility for clearer errors
    try:
        with open(secrets_path) as f:
            secrets_data = json.load(f)
        secrets_type = "web" if "web" in secrets_data else "installed" if "installed" in secrets_data else "unknown"
        allowed_redirects = []
        if secrets_type == "web":
            allowed_redirects = secrets_data.get("web", {}).get("redirect_uris", [])
        elif secrets_type == "installed":
            allowed_redirects = secrets_data.get("installed", {}).get("redirect_uris", [])
    except Exception:
        secrets_type = "unknown"
        allowed_redirects = []

    if secrets_type == "installed" and redirect_uri not in allowed_redirects:
        raise HTTPException(
            status_code=400,
            detail=(
                "Your credentials.json is an 'installed' client and does not authorize the server callback URI. "
                "Create a 'Web application' OAuth client in Google Cloud with the authorized redirect URI set to "
                f"{redirect_uri}, download the JSON, and replace credentials.json."
            ),
        )

    try:
        # Build a Web flow bound to the exact redirect URI and exchange using the full callback URL
        flow = Flow.from_client_secrets_file(
            secrets_path, scopes=scopes, redirect_uri=redirect_uri
        )
        flow.fetch_token(authorization_response=str(request.url))
        credentials = flow.credentials
    except Exception as exc:  # pragma: no cover - network/Google dependent
        raise HTTPException(status_code=500, detail=f"OAuth exchange failed: {exc}")

    # Prefer explicit token path; else save next to the client secrets file to avoid confusion
    token_path = os.getenv("GMAIL_TOKEN_PATH") or os.path.join(
        os.path.dirname(secrets_path) if secrets_path else _gmail_creds_dir(),
        "token.json",
    )
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    try:
        with open(token_path, "w") as f:
            f.write(credentials.to_json())
    except Exception as exc:  # pragma: no cover - filesystem errors
        raise HTTPException(status_code=500, detail=f"Saving token failed: {exc}")

    if state:
        try:
            agent_root = os.getenv("GOOGLE_AGENT_CREDENTIALS_DIR")
            if not agent_root:
                agent_root = os.path.join(os.getcwd(), ".credentials", "google")
            agent_dir = Path(agent_root) / state
            agent_dir.mkdir(parents=True, exist_ok=True)
            (agent_dir / "token.json").write_text(credentials.to_json())
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Saving agent token failed: {exc}")

    if state:
        email_for_store: Optional[str] = None
        try:
            from google.auth.transport.requests import AuthorizedSession  # type: ignore

            authed = AuthorizedSession(credentials)
            r = authed.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=10)
            if r.ok:
                profile = r.json()
                email_for_store = profile.get("emailAddress")
        except Exception:
            pass
        try:
            save_agent_google_token(
                agent_id=state,
                email=email_for_store or "unknown@googleuser.local",
                token=json.loads(credentials.to_json()),
            )
        except Exception:
            pass

    # Attempt to hot-reload Gmail tools (optional; can be slow). Enable with OAUTH_HOT_RELOAD=true
    if os.getenv("OAUTH_HOT_RELOAD", "false").lower() == "true":
        try:  # pragma: no cover - best-effort
            import importlib
            from agents.tools import gmail as gmail_mod
            import agents.tools.registry as registry

            importlib.reload(gmail_mod)
            registry.TOOL_REGISTRY["gmail"] = gmail_mod.gmail_tool
            registry.TOOL_REGISTRY["gmail_search"] = gmail_mod.gmail_search_tool
            registry.TOOL_REGISTRY["gmail_send_message"] = gmail_mod.gmail_send_message_tool
            registry.TOOL_REGISTRY["gmail_read_messages"] = gmail_mod.gmail_read_messages_tool
            registry.TOOL_REGISTRY["gmail_get_message"] = gmail_mod.gmail_get_message_tool
            registry.TOOL_REGISTRY["gmail_read"] = gmail_mod.gmail_read_messages_tool
            registry.TOOL_REGISTRY["gmail_read_inbox"] = gmail_mod.gmail_read_messages_tool
            registry.TOOL_REGISTRY["gmail_get"] = gmail_mod.gmail_get_message_tool
            registry.TOOL_REGISTRY["gmail_message"] = gmail_mod.gmail_get_message_tool
        except Exception:
            pass

    return GmailCallbackResponse(status="ok", detail=token_path, agent_id=state)


# -----------------------------
# Google Calendar OAuth callback
# -----------------------------
class CalendarCallbackResponse(BaseModel):
    status: str
    detail: Optional[str] = None
    agent_id: Optional[str] = None
    provider: Optional[str] = None


def _calendar_creds_dir() -> str:
    base_dir = os.getenv("GCAL_CREDENTIALS_DIR")
    if base_dir:
        return base_dir
    base_dir = os.getenv("CREDENTIALS_DIR")
    if base_dir:
        return os.path.join(base_dir, "calendar")
    return os.path.join(os.getcwd(), ".credentials", "calendar")


@router.get("/oauth/calendar/callback", response_model=CalendarCallbackResponse)
async def calendar_oauth_callback(request: Request):
    if Flow is None:
        raise HTTPException(status_code=500, detail="google-auth-oauthlib not installed")

    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    creds_dir = _calendar_creds_dir()
    secrets_candidates = [
        os.getenv("GCAL_CLIENT_SECRETS_PATH"),
        # If GCAL_CREDENTIALS_PATH is a dir, append credentials.json
        (os.path.join(os.getenv("GCAL_CREDENTIALS_PATH", ""), "credentials.json")
         if os.getenv("GCAL_CREDENTIALS_PATH") and os.path.isdir(os.getenv("GCAL_CREDENTIALS_PATH", ""))
         else os.getenv("GCAL_CREDENTIALS_PATH")),
        os.path.join(creds_dir, "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
    ]
    secrets_path = next((p for p in secrets_candidates if p and os.path.exists(p)), None)
    redirect_uri = os.getenv("GCAL_REDIRECT_URI") or os.getenv("CALENDAR_REDIRECT_URI")
    scopes_env = os.getenv("GCAL_SCOPES", os.getenv("CALENDAR_SCOPES", "https://www.googleapis.com/auth/calendar")).split(",")
    scopes_cb = params.get("scope")
    scopes = [s for s in (scopes_cb.split() if scopes_cb else scopes_env) if s]

    if not secrets_path or not os.path.exists(secrets_path):
        raise HTTPException(status_code=500, detail=f"Missing client secrets at {secrets_path}")
    if not redirect_uri:
        host = request.headers.get("host")
        if not host:
            raise HTTPException(status_code=500, detail="GCAL_REDIRECT_URI not configured")
        redirect_uri = f"{request.url.scheme}://{host}{request.url.path}"

    try:
        with open(secrets_path) as f:
            secrets_data = json.load(f)
        secrets_type = "web" if "web" in secrets_data else "installed" if "installed" in secrets_data else "unknown"
        allowed_redirects = []
        if secrets_type == "web":
            allowed_redirects = secrets_data.get("web", {}).get("redirect_uris", [])
        elif secrets_type == "installed":
            allowed_redirects = secrets_data.get("installed", {}).get("redirect_uris", [])
    except Exception:
        secrets_type = "unknown"
        allowed_redirects = []

    if secrets_type == "installed" and redirect_uri not in allowed_redirects:
        raise HTTPException(
            status_code=400,
            detail=(
                "Your credentials.json is an 'installed' client and does not authorize the server callback URI. "
                "Create a 'Web application' OAuth client in Google Cloud with the authorized redirect URI set to "
                f"{redirect_uri}, download the JSON, and replace credentials.json."
            ),
        )

    try:
        flow = Flow.from_client_secrets_file(
            secrets_path, scopes=scopes, redirect_uri=redirect_uri
        )
        flow.fetch_token(authorization_response=str(request.url))
        credentials = flow.credentials
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calendar OAuth exchange failed: {exc}")

    # Prefer explicit token path; else save next to the client secrets file to avoid confusion
    token_path = os.getenv("GCAL_TOKEN_PATH") or os.path.join(
        os.path.dirname(secrets_path) if secrets_path else _calendar_creds_dir(),
        "calendar_token.json",
    )
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    try:
        with open(token_path, "w") as f:
            f.write(credentials.to_json())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Saving calendar token failed: {exc}")

    # Try to hot-reload calendar tools (optional)
    if os.getenv("OAUTH_HOT_RELOAD", "false").lower() == "true":
        try:
            import importlib
            from agents.tools import google_calendar as gcal_mod
            import agents.tools.registry as registry

            importlib.reload(gcal_mod)
            tools_list = gcal_mod.initialize_calendar_tools(
                credentials_file=secrets_path,
                token_file=token_path,
                timezone=os.getenv("GCAL_TIMEZONE", "Asia/Jakarta"),
            )
            for t in tools_list:
                registry.TOOL_REGISTRY[t.name] = t
            # also expose unified alias
            registry.TOOL_REGISTRY.setdefault("calendar", next((t for t in tools_list if getattr(t, "name", "") == "calendar"), None))
        except Exception:
            pass

    return CalendarCallbackResponse(status="ok", detail=token_path, agent_id=state, provider="calendar")


# -----------------------------
# Universal Google OAuth callback (Gmail + Calendar)
# -----------------------------
@router.get("/oauth/google/callback", response_model=CalendarCallbackResponse)
async def google_oauth_callback(request: Request):
    """
    Accepts OAuth callback for both Gmail and Calendar and writes tokens to the
    appropriate provider paths. This lets you maintain a single redirect URI.

    Env resolution order for client secrets:
      - GMAIL_CLIENT_SECRETS_PATH
      - GCAL_CLIENT_SECRETS_PATH
      - credential folders (gmail/calendar)
      - ./credentials.json
      - GOOGLE_APPLICATION_CREDENTIALS

    Env for redirect URI (for constructing the Flow):
      - GOOGLE_OAUTH_REDIRECT_URI or OAUTH_REDIRECT_URI
      - GMAIL_REDIRECT_URI or GCAL_REDIRECT_URI
      - fallback to the current request URL
    """
    if Flow is None:
        raise HTTPException(status_code=500, detail="google-auth-oauthlib not installed")

    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")
    scopes_cb = params.get("scope", "")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    # Determine provider from state if annotated, else infer from scopes
    provider: Optional[str] = None
    if state and ":" in state:
        # e.g., "gmail:agentid" or "calendar:agentid"
        maybe_provider, _, rest = state.partition(":")
        if maybe_provider in {"gmail", "calendar"}:
            provider = maybe_provider
            state = rest or None
    if not provider:
        if any("gmail" in s or "mail.google.com" in s for s in scopes_cb.split()):
            provider = "gmail"
        elif any("calendar" in s for s in scopes_cb.split()):
            provider = "calendar"
        elif any("documents" in s or "drive" in s for s in scopes_cb.split()):
            provider = "docs"

    # Resolve secrets path with broad fallbacks (prefer explicit files)
    def _path_exists(p: Optional[str]) -> bool:
        return bool(p and os.path.exists(p))

    gmail_dir = _gmail_creds_dir()
    cal_dir = _calendar_creds_dir()
    secrets_candidates = [
        os.getenv("GMAIL_CLIENT_SECRETS_PATH"),
        os.getenv("GCAL_CLIENT_SECRETS_PATH"),
        os.path.join(gmail_dir, "credentials.json"),
        os.path.join(cal_dir, "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    ]
    secrets_path = next((p for p in secrets_candidates if _path_exists(p)), None)
    if not secrets_path:
        raise HTTPException(status_code=500, detail="Missing client secrets file for Google OAuth")

    # Redirect URI determination
    redirect_uri = (
        os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        or os.getenv("OAUTH_REDIRECT_URI")
        or os.getenv("GMAIL_REDIRECT_URI")
        or os.getenv("GCAL_REDIRECT_URI")
    )
    if not redirect_uri:
        # Fallback: construct from current request URL
        host = request.headers.get("host")
        if not host:
            raise HTTPException(status_code=500, detail="Redirect URI not configured")
        redirect_uri = f"{request.url.scheme}://{host}{request.url.path}"

    # Build flow bound to the exact redirect used
    try:
        flow = Flow.from_client_secrets_file(secrets_path, scopes=scopes_cb.split(), redirect_uri=redirect_uri)
        flow.fetch_token(authorization_response=str(request.url))
        credentials = flow.credentials
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OAuth exchange failed: {exc}")

    # Decide token path(s)
    written_paths: list[str] = []
    # If provider is known and explicit, write only that; otherwise write to both when scopes include both
    try:
        if provider in {None, "gmail"} and any(
            ("gmail" in s) or ("mail.google.com" in s) for s in (credentials.scopes or [])
        ):
            gtok = os.getenv("GMAIL_TOKEN_PATH") or os.path.join(os.path.dirname(secrets_path), "token.json")
            os.makedirs(os.path.dirname(gtok), exist_ok=True)
            with open(gtok, "w") as f:
                f.write(credentials.to_json())
            written_paths.append(gtok)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Saving Gmail token failed: {exc}")

    try:
        if provider in {None, "calendar"} and any(
            "calendar" in s for s in (credentials.scopes or [])
        ):
            ctok = os.getenv("GCAL_TOKEN_PATH") or os.path.join(os.path.dirname(secrets_path), "calendar_token.json")
            os.makedirs(os.path.dirname(ctok), exist_ok=True)
            with open(ctok, "w") as f:
                f.write(credentials.to_json())
            written_paths.append(ctok)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Saving Calendar token failed: {exc}")

    try:
        if provider in {None, "docs"} and any(
            ("documents" in s) or ("drive" in s) for s in (credentials.scopes or [])
        ):
            dtok = os.getenv("GDOCS_TOKEN_PATH") or os.path.join(os.path.dirname(secrets_path), "docs_token.json")
            os.makedirs(os.path.dirname(dtok), exist_ok=True)
            with open(dtok, "w") as f:
                f.write(credentials.to_json())
            written_paths.append(dtok)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Saving Docs token failed: {exc}")

    agent_token_path: Path | None = None
    if state:
        try:
            agent_root = os.getenv("GOOGLE_AGENT_CREDENTIALS_DIR")
            if not agent_root:
                agent_root = os.path.join(os.getcwd(), ".credentials", "google")
            agent_dir = Path(agent_root) / state
            agent_dir.mkdir(parents=True, exist_ok=True)
            agent_token_path = agent_dir / "token.json"
            agent_token_path.write_text(credentials.to_json())
            written_paths.append(str(agent_token_path))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Saving agent token failed: {exc}")

    # Persist a unified token per agent (list_account) if state carries agent_id
    # Try to obtain the Google account email via Gmail profile when scopes allow
    email_for_store: Optional[str] = None
    try:
        from google.auth.transport.requests import AuthorizedSession  # type: ignore
        granted_scopes = list(credentials.scopes or [])
        if any(("gmail" in s) or ("mail.google.com" in s) for s in granted_scopes):
            try:
                authed = AuthorizedSession(credentials)
                r = authed.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=10)
                if r.ok:
                    profile = r.json()
                    email_for_store = profile.get("emailAddress")
            except Exception:
                pass
    except Exception:
        pass

    if state:
        try:
            save_agent_google_token(
                agent_id=state,
                email=email_for_store or "unknown@googleuser.local",
                token=json.loads(credentials.to_json()),
            )
        except Exception:
            # Do not block OAuth flow on DB errors
            pass

    # Try to hot-reload tools (optional to avoid slow callbacks)
    if os.getenv("OAUTH_HOT_RELOAD", "false").lower() == "true":
        try:  # pragma: no cover - best effort
            import importlib
            import agents.tools.registry as registry
            from agents.tools import gmail as gmail_mod
            from agents.tools import google_calendar as gcal_mod
            importlib.reload(gmail_mod)
            importlib.reload(gcal_mod)
            registry.TOOL_REGISTRY["gmail"] = getattr(gmail_mod, "gmail_tool", registry.TOOL_REGISTRY.get("gmail"))
            for n in [
                "gmail_search",
                "gmail_send_message",
                "gmail_read_messages",
                "gmail_get_message",
                "gmail_read",
                "gmail_read_inbox",
                "gmail_get",
                "gmail_message",
            ]:
                if hasattr(gmail_mod, n + "_tool"):
                    registry.TOOL_REGISTRY[n] = getattr(gmail_mod, n + "_tool")
            # Refresh calendar tools
            tools_list = gcal_mod.initialize_calendar_tools(
                credentials_file=secrets_path,
                token_file=os.getenv("GCAL_TOKEN_PATH") or os.path.join(os.path.dirname(secrets_path), "calendar_token.json"),
                timezone=os.getenv("GCAL_TIMEZONE", "Asia/Jakarta"),
            )
            for t in tools_list:
                registry.TOOL_REGISTRY[t.name] = t
            registry.TOOL_REGISTRY.setdefault(
                "calendar", next((t for t in tools_list if getattr(t, "name", "") == "calendar"), None)
            )
            # Refresh docs tools (best effort)
            try:
                from agents.tools import google_docs as gdocs_mod
                importlib.reload(gdocs_mod)
                dtools = gdocs_mod.initialize_docs_tools(
                    credentials_file=secrets_path,
                    token_file=os.getenv("GDOCS_TOKEN_PATH") or os.path.join(os.path.dirname(secrets_path), "docs_token.json"),
                )
                for t in dtools:
                    registry.TOOL_REGISTRY[t.name] = t
                registry.TOOL_REGISTRY.setdefault(
                    "google_docs", next((t for t in dtools if getattr(t, "name", "") == "google_docs"), None)
                )
                registry.TOOL_REGISTRY.setdefault("docs", registry.TOOL_REGISTRY.get("google_docs"))
            except Exception:
                pass
        except Exception:
            pass

    # Prefer reporting the most relevant provider if we inferred both
    prov = provider or ("gmail" if any("gmail" in s for s in scopes_cb.split()) else ("calendar" if any("calendar" in s for s in scopes_cb.split()) else "docs"))
    return CalendarCallbackResponse(status="ok", detail=", ".join(written_paths), agent_id=state, provider=prov)
