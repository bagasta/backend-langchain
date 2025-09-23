# Tool registry
# agents/tools/registry.py

from .google_search import google_search_tool
from .google_serper import google_serper_tool
from .google_trends import google_trends_tool
from .google_places import google_places_tool
from .google_finance import google_finance_tool
from .google_cloud_text_to_speech import google_cloud_text_to_speech_tool
from .google_jobs import google_jobs_tool
from .google_scholar import google_scholar_tool
from .google_books import google_books_tool
from .google_lens import google_lens_tool
from .google_maps import google_maps_tool, maps_tools
from .google_docs import build_docs_oauth_url
from .gmail import (
    gmail_search_tool,
    gmail_send_message_tool,
    gmail_read_messages_tool,
    gmail_get_message_tool,
    gmail_tool,
    build_gmail_oauth_url,
)
from .google_calendar import build_calendar_oauth_url
import os

# Avoid importing heavy/interactive calendar module at import time. We'll lazy-load it.
_CALENDAR_TOOL_NAMES = {
    "calendar",
    "google_calendar",
    "create_calendar_event",
    "list_calendar_events",
    "get_calendar_event",
    "update_calendar_event",
    "delete_calendar_event",
    "search_calendar_events",
    "get_free_busy",
    "list_calendars",
}
from .calc import calc_tool
from .websearch import websearch_tool
from .spreadsheet import spreadsheet_tool

# Register all tools here; key = name used in config.tools
TOOL_REGISTRY = {
    "google_search": google_search_tool,
    "google": google_search_tool,  # backward-compat alias
    "google_serper": google_serper_tool,
    "google_trends": google_trends_tool,
    "google_places": google_places_tool,
    "google_finance": google_finance_tool,
    "google_cloud_text_to_speech": google_cloud_text_to_speech_tool,
    "google_jobs": google_jobs_tool,
    "google_scholar": google_scholar_tool,
    "google_books": google_books_tool,
    "google_lens": google_lens_tool,
    "google_maps": google_maps_tool,
    # Common aliases users may type
    "maps": google_maps_tool,
    "directions": next((t for t in maps_tools if getattr(t, "name", "") == "maps_directions"), None),
    "geocode": next((t for t in maps_tools if getattr(t, "name", "") == "maps_geocode"), None),
    "distance_matrix": next((t for t in maps_tools if getattr(t, "name", "") == "maps_distance_matrix"), None),
    # Convenience aliases for Google Maps
    "maps_geocode": next((t for t in maps_tools if getattr(t, "name", "") == "maps_geocode"), None),
    "maps_directions": next((t for t in maps_tools if getattr(t, "name", "") == "maps_directions"), None),
    "maps_distance_matrix": next((t for t in maps_tools if getattr(t, "name", "") == "maps_distance_matrix"), None),
    "maps_nearby": next((t for t in maps_tools if getattr(t, "name", "") == "maps_nearby"), None),
    # Gmail (canonical umbrella name will be google_gmail; keep gmail as alias)
    "gmail": gmail_tool,
    "google_gmail": gmail_tool,
    "gmail_search": gmail_search_tool,
    "gmail_send_message": gmail_send_message_tool,
    "gmail_read_messages": gmail_read_messages_tool,
    "gmail_get_message": gmail_get_message_tool,
    # helpful aliases for reading inbox
    "gmail_read": gmail_read_messages_tool,
    "gmail_read_inbox": gmail_read_messages_tool,
    "gmail_get": gmail_get_message_tool,
    "gmail_message": gmail_get_message_tool,
    # helpful aliases for LLMs
    "send_email": gmail_send_message_tool,
    "email_send": gmail_send_message_tool,
    "calc": calc_tool,
    # Web search + common aliases (normalized in expand function too)
    "websearch": websearch_tool,
    "web_search": websearch_tool,
    "web search": websearch_tool,
    "spreadsheet": spreadsheet_tool,
    # Google Docs tools (lazy-initialized)
    "google_docs": None,
    "docs": None,
    "docs_create": None,
    "docs_get": None,
    "docs_append": None,
    "docs_export_pdf": None,
    # Google Calendar tools (lazy-initialized)
    "calendar": None,
    "google_calendar": None,
    "create_calendar_event": None,
    "list_calendar_events": None,
    "get_calendar_event": None,
    "update_calendar_event": None,
    "delete_calendar_event": None,
    "search_calendar_events": None,
    "get_free_busy": None,
    "list_calendars": None,
}

# Mapping of tools to optional OAuth login URL builders.
AUTH_URL_BUILDERS = {
    "gmail": build_gmail_oauth_url,
    "google_gmail": build_gmail_oauth_url,
    "gmail_search": build_gmail_oauth_url,
    "gmail_send_message": build_gmail_oauth_url,
    "gmail_read_messages": build_gmail_oauth_url,
    "gmail_get_message": build_gmail_oauth_url,
    "gmail_read": build_gmail_oauth_url,
    "gmail_read_inbox": build_gmail_oauth_url,
    "gmail_get": build_gmail_oauth_url,
    "gmail_message": build_gmail_oauth_url,
    # Calendar
    "calendar": build_calendar_oauth_url,
    "google_calendar": build_calendar_oauth_url,
    "create_calendar_event": build_calendar_oauth_url,
    "list_calendar_events": build_calendar_oauth_url,
    "get_calendar_event": build_calendar_oauth_url,
    "update_calendar_event": build_calendar_oauth_url,
    "delete_calendar_event": build_calendar_oauth_url,
    "search_calendar_events": build_calendar_oauth_url,
    "get_free_busy": build_calendar_oauth_url,
    "list_calendars": build_calendar_oauth_url,
    # Docs
    "google_docs": build_docs_oauth_url,
    "docs": build_docs_oauth_url,
    "docs_create": build_docs_oauth_url,
    "docs_get": build_docs_oauth_url,
    "docs_append": build_docs_oauth_url,
    "docs_export_pdf": build_docs_oauth_url,
}

def expand_tool_names(names: list[str]) -> list[str]:
    """Expand shorthand or partial sets into a complete set of tools.

    Rules:
    - If any Gmail indicator is present (e.g., 'gmail', 'gmail_search', 'gmail_send_message',
      'gmail_read_messages', 'gmail_read', 'gmail_read_inbox', 'send_email', 'email_send'),
      include both 'gmail_send_message' and 'gmail_read_messages'.
    - Keep original non-Gmail tool names.
    - Keep the unified 'gmail' tool if present; it can be used directly.
    """
    # Normalize and split inputs (accept comma/semicolon/pipe-delimited strings)
    tokens: list[str] = []
    for raw in (names or []):
        if not raw or not isinstance(raw, str):
            continue
        parts = [raw]
        if any(d in raw for d in [",", ";", "|"]):
            tmp = []
            for p in parts:
                for d in [",", ";", "|"]:
                    p = p.replace(d, ",")
                tmp.extend([s for s in p.split(",") if s is not None])
            parts = tmp
        for p in parts:
            s = (p or "").strip()
            if not s:
                continue
            tokens.append(s)

    # Canonicalize: lowercase, replace spaces/hyphens with underscores
    canonical: list[str] = []
    for t in tokens:
        s = t.strip().lower()
        for ch in [" ", "-", "—", "–"]:
            s = s.replace(ch, "_")
        while "__" in s:
            s = s.replace("__", "_")
        s = s.strip("_")
        # Synonym mapping to canonical keys
        synonyms = {
            # Gmail
            "gmail": "google_gmail",
            "google_gmail": "google_gmail",
            "g_mail": "google_gmail",
            # Calendar
            "calendar": "google_calendar",
            "google_calendar": "google_calendar",
            "google_cal": "google_calendar",
            # Docs
            "google_docs": "google_docs",
            "docs": "google_docs",
            "google_doc": "google_docs",
            "google_documents": "google_docs",
            # Maps
            "google_maps": "google_maps",
            "maps": "maps",
            # Search
            "web": "websearch",
            "web_search": "websearch",
            "websearch": "websearch",
            "websearch_tool": "websearch",
            "web-search": "websearch",
            "google_search": "google_search",
            "google": "google_search",
            "search": "google_search",
            # Serper
            "serper": "google_serper",
            "google_serper": "google_serper",
        }
        canonical.append(synonyms.get(s, s))

    base = canonical
    lower = {n.lower() for n in base}
    gmail_triggers = {
        "gmail",
        "google_gmail",
        "gmail_search",
        "gmail_send_message",
        "gmail_read_messages",
        "gmail_get_message",
        "gmail_read",
        "gmail_read_inbox",
        "gmail_get",
        "gmail_message",
        "send_email",
        "email_send",
    }
    expanded = list(base)  # keep unified tools as-is
    if lower & gmail_triggers:
        # Prefer read/get tools before send to reduce accidental sends
        if "gmail_read_messages" not in expanded:
            expanded.append("gmail_read_messages")
        if "gmail_get_message" not in expanded:
            expanded.append("gmail_get_message")
        if "gmail_send_message" not in expanded:
            expanded.append("gmail_send_message")
    # Expand Google Calendar umbrella name into concrete tools
    if "google_calendar" in lower:
        for n in [
            "create_calendar_event",
            "list_calendar_events",
            "get_calendar_event",
            "update_calendar_event",
            "delete_calendar_event",
            "search_calendar_events",
            "get_free_busy",
            "list_calendars",
        ]:
            if n not in expanded:
                expanded.append(n)
        # Remove umbrella alias 'google_calendar' (keep 'calendar' unified if requested)
        expanded = [n for n in expanded if n.lower() not in {"google_calendar"}]
    # Expand Google Maps unified or shorthand 'maps' name to include convenience aliases
    if ("google_maps" in lower) or ("maps" in lower):
        for n in [
            "maps_geocode",
            "maps_directions",
            "maps_distance_matrix",
            "maps_nearby",
        ]:
            if n not in expanded:
                expanded.append(n)
        # Normalize: remove umbrella shorthand 'maps' in final list
        expanded = [n for n in expanded if n.lower() not in {"maps"}]
    # Normalize and expand Google Docs umbrella
    if ("google_docs" in lower) or ("docs" in lower):
        for n in ["docs_create", "docs_get", "docs_append", "docs_export_pdf"]:
            if n not in expanded:
                expanded.append(n)
        expanded = [n for n in expanded if n.lower() not in {"docs", "google_docs"}] + [
            # keep unified tool last for LLM choice
            "google_docs"
        ]
    # De-duplicate while preserving order
    seen = set()
    out: list[str] = []
    for n in expanded:
        nl = n.lower()
        if nl in seen:
            continue
        seen.add(nl)
        out.append(n)
    return out


def get_tools_by_names(names: list[str]):
    """
    Return a list of tool instances for the given names, ignoring unknown ones.
    """
    final_names = expand_tool_names(names)
    tools = []
    for name in final_names:
        name_lower = name.lower()
        tool = TOOL_REGISTRY.get(name) or TOOL_REGISTRY.get(name_lower)
        # Lazy self-heal for Gmail entries if they were None at import time
        if tool is None and name_lower.startswith("gmail"):
            try:
                import importlib
                from . import gmail as gmail_mod

                importlib.reload(gmail_mod)
                # Refresh all gmail-related entries atomically
                TOOL_REGISTRY["gmail"] = getattr(gmail_mod, "gmail_tool", TOOL_REGISTRY.get("gmail"))
                TOOL_REGISTRY["gmail_search"] = getattr(
                    gmail_mod, "gmail_search_tool", TOOL_REGISTRY.get("gmail_search")
                )
                TOOL_REGISTRY["gmail_send_message"] = getattr(
                    gmail_mod, "gmail_send_message_tool", TOOL_REGISTRY.get("gmail_send_message")
                )
                TOOL_REGISTRY["gmail_read_messages"] = getattr(
                    gmail_mod, "gmail_read_messages_tool", TOOL_REGISTRY.get("gmail_read_messages")
                )
                TOOL_REGISTRY["gmail_get_message"] = getattr(
                    gmail_mod, "gmail_get_message_tool", TOOL_REGISTRY.get("gmail_get_message")
                )
                # re-fetch
                tool = TOOL_REGISTRY.get(name) or TOOL_REGISTRY.get(name_lower)
            except Exception:
                pass
        # Lazy init for Google Calendar tools
        if (tool is None or (
            name_lower in _CALENDAR_TOOL_NAMES and isinstance(tool, object) and 
            getattr(tool, "description", "").lower().startswith("google calendar stub tool")
        )) and name_lower in _CALENDAR_TOOL_NAMES:
            try:
                import importlib
                from . import google_calendar as gcal_mod
                # Initialize tools using env overrides if provided
                creds_path = os.getenv("GCAL_CREDENTIALS_PATH") or os.path.join(
                    os.getcwd(), "credentials.json"
                )
                # If a directory is provided, assume credentials.json inside it
                try:
                    import os as _os
                    if _os.path.isdir(creds_path):
                        creds_path = _os.path.join(creds_path, "credentials.json")
                except Exception:
                    pass
                timezone = os.getenv("GCAL_TIMEZONE", "Asia/Jakarta")
                token_path = os.getenv("GCAL_TOKEN_PATH")  # optional; defaults inside initializer
                # If a directory is provided for token, use calendar_token.json inside it
                try:
                    if token_path and _os.path.isdir(token_path):
                        token_path = _os.path.join(token_path, "calendar_token.json")
                except Exception:
                    pass
                tools_list = gcal_mod.initialize_calendar_tools(
                    credentials_file=creds_path, timezone=timezone, token_file=token_path
                )
                # Map by name
                for t in tools_list:
                    TOOL_REGISTRY[t.name] = t
                tool = TOOL_REGISTRY.get(name) or TOOL_REGISTRY.get(name_lower)
            except Exception as e:
                print(f"[WARNING] Failed to initialize Google Calendar tools: {e}")
                # Provide graceful stub tools so the agent can still respond
                try:
                    try:
                        from langchain_core.tools import Tool as CoreTool  # type: ignore
                    except Exception:  # pragma: no cover
                        from langchain.agents import Tool as CoreTool  # type: ignore

                    error_msg = (
                        "Google Calendar tool unavailable: "
                        + str(e)
                        + ". Ensure Calendar API is enabled and credentials/token have the right scopes."
                    )

                    def _calendar_stub(_input: str = ""):
                        return error_msg

                    # Register stubs for unified and common ops
                    for n in [
                        "calendar",
                        "create_calendar_event",
                        "list_calendar_events",
                        "get_calendar_event",
                        "update_calendar_event",
                        "delete_calendar_event",
                        "search_calendar_events",
                        "get_free_busy",
                        "list_calendars",
                    ]:
                        if TOOL_REGISTRY.get(n) is None:
                            TOOL_REGISTRY[n] = CoreTool(
                                name=n,
                                description=(
                                    "Google Calendar stub tool (init failed). Calls return an explanatory error."
                                ),
                                func=_calendar_stub,
                            )
                    tool = TOOL_REGISTRY.get(name)
                except Exception:
                    pass
        # Lazy init for Google Docs tools
        _DOC_TOOL_NAMES = {
            "google_docs", "docs", "docs_create", "docs_get", "docs_append", "docs_export_pdf"
        }
        if tool is None and name_lower in _DOC_TOOL_NAMES:
            try:
                import importlib
                from . import google_docs as gdocs_mod
                importlib.reload(gdocs_mod)
                # Credentials
                creds_path = os.getenv("GDOCS_CREDENTIALS_PATH") or os.getenv("GCAL_CREDENTIALS_PATH") or os.path.join(
                    os.getcwd(), "credential_folder", "credentials.json"
                )
                try:
                    import os as _os
                    if _os.path.isdir(creds_path):
                        creds_path = _os.path.join(creds_path, "credentials.json")
                except Exception:
                    pass
                token_path = os.getenv("GDOCS_TOKEN_PATH")
                tools_list = gdocs_mod.initialize_docs_tools(credentials_file=creds_path, token_file=token_path)
                for t in tools_list:
                    TOOL_REGISTRY[t.name] = t
                # expose unified aliases
                TOOL_REGISTRY["google_docs"] = next((t for t in tools_list if getattr(t, "name", "") == "google_docs"), None)
                TOOL_REGISTRY["docs"] = TOOL_REGISTRY["google_docs"]
                tool = TOOL_REGISTRY.get(name) or TOOL_REGISTRY.get(name_lower)
            except Exception as e:
                print(f"[WARNING] Failed to initialize Google Docs tools: {e}")
                try:
                    from langchain.agents import Tool as CoreTool  # type: ignore
                    err = f"Google Docs tool unavailable: {e}. Ensure Docs & Drive APIs enabled and scopes authorized."
                    def _stub(_input: str = ""):
                        return err
                    for n in ["google_docs", "docs", "docs_create", "docs_get", "docs_append", "docs_export_pdf"]:
                        if TOOL_REGISTRY.get(n) is None:
                            TOOL_REGISTRY[n] = CoreTool(name=n, func=_stub, description="Google Docs stub tool (init failed)")
                    tool = TOOL_REGISTRY.get(name)
                except Exception:
                    pass
        if tool:
            tools.append(tool)
        else:
            # optional: log or raise error for unknown tool
            print(f"[WARNING] Tool '{name}' tidak ditemukan di registry")
    return tools


def _build_unified_google_oauth_url(state: str | None = None) -> str | None:
    """Build a single Google OAuth URL with union scopes (Gmail + Calendar + Docs).

    Uses GOOGLE_OAUTH_REDIRECT_URI (or OAUTH_REDIRECT_URI) and the first
    available client secrets among Gmail/Calendar/Docs candidates.
    """
    import os
    import json
    from urllib.parse import urlencode
    # Candidate secrets across providers
    cands = [
        os.getenv("GMAIL_CLIENT_SECRETS_PATH"),
        os.getenv("GCAL_CLIENT_SECRETS_PATH"),
        (os.path.join(os.getenv("GCAL_CREDENTIALS_PATH", ""), "credentials.json")
         if os.getenv("GCAL_CREDENTIALS_PATH") and os.path.isdir(os.getenv("GCAL_CREDENTIALS_PATH", ""))
         else os.getenv("GCAL_CREDENTIALS_PATH")),
        os.getenv("GDOCS_CLIENT_SECRETS_PATH"),
        (os.path.join(os.getenv("GDOCS_CREDENTIALS_PATH", ""), "credentials.json")
         if os.getenv("GDOCS_CREDENTIALS_PATH") and os.path.isdir(os.getenv("GDOCS_CREDENTIALS_PATH", ""))
         else os.getenv("GDOCS_CREDENTIALS_PATH")),
        os.path.join(os.getcwd(), "credential_folder", "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    ]
    secrets_path = next((p for p in cands if p and os.path.exists(p)), None)
    redirect_uri = (
        os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        or os.getenv("OAUTH_REDIRECT_URI")
    )
    if not secrets_path or not redirect_uri or not os.path.exists(secrets_path):
        return None
    try:
        with open(secrets_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cid = (data.get("web", {}) or {}).get("client_id") or (data.get("installed", {}) or {}).get("client_id")
        if not cid:
            return None
    except Exception:
        return None
    # Union scopes from modules
    try:
        from .gmail import SCOPES as GMAIL_SCOPES
    except Exception:
        GMAIL_SCOPES = []
    try:
        from .google_calendar import SCOPES as GCAL_SCOPES
    except Exception:
        GCAL_SCOPES = []
    try:
        from .google_docs import SCOPES as GDOCS_SCOPES
    except Exception:
        GDOCS_SCOPES = []
    scopes = []
    for s in list(GMAIL_SCOPES) + list(GCAL_SCOPES) + list(GDOCS_SCOPES):
        s = (s or "").strip()
        if s and s not in scopes:
            scopes.append(s)
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def get_auth_urls(names: list[str], state: str | None = None) -> dict[str, str]:
    """Return OAuth login URLs for selected tools.

    Only tools that define an auth URL builder are included. The returned dict
    maps a short provider name (e.g. ``gmail``) to the URL.
    """

    final_names = expand_tool_names(names)
    urls: dict[str, str] = {}

    # Detect any Google providers among the requested tools
    lower = {n.lower() for n in final_names}
    doc_tool_names = {"google_docs", "docs", "docs_create", "docs_get", "docs_append", "docs_export_pdf"}
    google_related = any(
        (n.startswith("gmail"))
        or (n.startswith("google_gmail"))
        or (n in _CALENDAR_TOOL_NAMES)
        or (n in doc_tool_names)
        for n in lower
    )

    unified_url: str | None = None
    if google_related:
        unified_url = _build_unified_google_oauth_url(state=state)
        if unified_url:
            urls["google"] = unified_url

    # Preserve provider-specific URLs whenever a unified Google URL
    # is unavailable. This ensures existing integrations keep working
    # even if only Gmail-specific environment variables are configured.
    for name in final_names:
        name_lower = name.lower()
        is_google_provider = (
            name_lower.startswith("gmail")
            or name_lower.startswith("google_gmail")
            or (name_lower in _CALENDAR_TOOL_NAMES)
            or (name_lower in doc_tool_names)
        )

        # Skip per-provider URLs only when a unified Google URL was generated.
        if is_google_provider and unified_url:
            continue

        builder = AUTH_URL_BUILDERS.get(name) or AUTH_URL_BUILDERS.get(name_lower)
        if builder:
            url = builder(state=state)
            if url:
                key = name_lower
                if name_lower.startswith("gmail") or name_lower.startswith("google_gmail"):
                    key = "gmail"
                elif name_lower in _CALENDAR_TOOL_NAMES:
                    key = "google_calendar"
                elif name_lower in doc_tool_names:
                    key = "google_docs"
                urls.setdefault(key, url)
    return urls
