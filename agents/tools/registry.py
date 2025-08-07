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
    "calc": calc_tool,
    "websearch": websearch_tool,
    "spreadsheet": spreadsheet_tool,
}

def get_tools_by_names(names: list[str]):
    """
    Return a list of tool instances for the given names, ignoring unknown ones.
    """
    tools = []
    for name in names:
        tool = TOOL_REGISTRY.get(name)
        if tool:
            tools.append(tool)
        else:
            # optional: log or raise error for unknown tool
            print(f"[WARNING] Tool '{name}' tidak ditemukan di registry")
    return tools
