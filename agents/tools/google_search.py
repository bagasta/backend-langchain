"""Google Search tool using LangChain wrappers.

If the Google Search wrapper cannot be initialized (missing API keys or
package dependencies), a stub tool is provided that explains the
misconfiguration instead of raising an exception at import time.
"""

from langchain.agents import Tool

try:  # pragma: no cover - depends on external API keys
    from langchain_community.tools.google_search import GoogleSearchRun

    google_search_tool = GoogleSearchRun()
except Exception as e:  # pragma: no cover - handled in tests
    err_msg = str(e)

    def _search_stub(query: str) -> str:
        return f"Google Search tool unavailable: {err_msg}"  # noqa: B023

    google_search_tool = Tool(
        name="google_search",
        func=_search_stub,
        description="Search the web for up-to-date information using Google",
    )

__all__ = ["google_search_tool"]
