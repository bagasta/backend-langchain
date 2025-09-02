from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import os
import json
from typing import Optional

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
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    ]
    secrets_path = next((p for p in secrets_candidates if p and os.path.exists(p)), None)
    redirect_uri = os.getenv("GMAIL_REDIRECT_URI")
    scopes = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send",
    ).split(",")

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

    os.makedirs(creds_dir, exist_ok=True)
    token_path = os.getenv("GMAIL_TOKEN_PATH") or os.path.join(creds_dir, "token.json")
    try:
        with open(token_path, "w") as f:
            f.write(credentials.to_json())
    except Exception as exc:  # pragma: no cover - filesystem errors
        raise HTTPException(status_code=500, detail=f"Saving token failed: {exc}")

    # Attempt to hot-reload Gmail tools so future agents can use them without restart
    try:  # pragma: no cover - best-effort
        import importlib
        from agents.tools import gmail as gmail_mod
        import agents.tools.registry as registry

        importlib.reload(gmail_mod)
        registry.TOOL_REGISTRY["gmail_search"] = gmail_mod.gmail_search_tool
        registry.TOOL_REGISTRY["gmail_send_message"] = gmail_mod.gmail_send_message_tool
    except Exception:
        pass

    return GmailCallbackResponse(status="ok", detail=token_path, agent_id=state)
