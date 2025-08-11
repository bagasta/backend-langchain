"""Gmail tools using LangChain wrappers.

This module exposes search and send-message tools backed by the Gmail API.
If credentials or dependencies are missing, stub tools are provided that
return a helpful error message instead of raising at import time.
"""

from langchain.agents import Tool
from urllib.parse import urlencode
import json
import os

try:  # pragma: no cover - depends on Google API setup
    from langchain_community.tools.gmail.utils import (
        get_gmail_credentials,
        build_resource_service,
    )
    from langchain_community.tools.gmail.search import GmailSearch
    from langchain_community.tools.gmail.send_message import GmailSendMessage

    token_path = os.getenv("GMAIL_TOKEN_PATH")
    client_secrets_path = os.getenv("GMAIL_CLIENT_SECRETS_PATH")
    scopes = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify",
    ).split(",")

    if not token_path or not client_secrets_path:
        raise ValueError(
            "GMAIL_TOKEN_PATH and GMAIL_CLIENT_SECRETS_PATH must be set"
        )

    creds = get_gmail_credentials(
        token_file=token_path,
        client_secrets_file=client_secrets_path,
        scopes=scopes,
    )
    service = build_resource_service(credentials=creds)

    gmail_search_tool = GmailSearch(api_resource=service)
    gmail_send_message_tool = GmailSendMessage(api_resource=service)
except Exception as e:  # pragma: no cover - handled in tests
    err_msg = str(e)

    def _gmail_stub(*args, **kwargs):
        return f"Gmail tool unavailable: {err_msg}"  # noqa: B023

    gmail_search_tool = Tool(
        name="gmail_search",
        func=_gmail_stub,
        description="Search emails in a Gmail account",
    )
    gmail_send_message_tool = Tool(
        name="gmail_send_message",
        func=_gmail_stub,
        description="Send an email via Gmail",
    )


def build_gmail_oauth_url(state: str | None = None) -> str | None:
    """Return an OAuth login URL for Gmail if credentials permit.

    The function reads ``GMAIL_CLIENT_SECRETS_PATH`` to extract a client ID and
    ``GMAIL_REDIRECT_URI`` for the OAuth callback. When either value is missing
    or the secrets file lacks a client ID, ``None`` is returned.
    """

    secrets_path = os.getenv("GMAIL_CLIENT_SECRETS_PATH")
    redirect_uri = os.getenv("GMAIL_REDIRECT_URI")
    scopes = os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify",
    ).split(",")
    if not secrets_path or not redirect_uri:
        return None

    try:
        with open(secrets_path) as f:
            data = json.load(f)
        client_id = (
            data.get("installed", {}).get("client_id")
            or data.get("web", {}).get("client_id")
        )
    except Exception:  # pragma: no cover - missing/invalid secrets
        return None
    if not client_id:
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

__all__ = [
    "gmail_search_tool",
    "gmail_send_message_tool",
    "build_gmail_oauth_url",
]
