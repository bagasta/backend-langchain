"""Gmail tools using LangChain wrappers.

This module exposes search and send-message tools backed by the Gmail API.
If credentials or dependencies are missing, stub tools are provided that
return a helpful error message instead of raising at import time.
"""

from langchain.agents import Tool
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

__all__ = ["gmail_search_tool", "gmail_send_message_tool"]
