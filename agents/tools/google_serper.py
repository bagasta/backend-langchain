"""Google Serper search tool."""
from langchain.agents import Tool

try:  # pragma: no cover - depends on external API
    from langchain_community.tools.google_serper import GoogleSerperRun

    google_serper_tool = GoogleSerperRun()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _serper_stub(query: str) -> str:
        return f"Google Serper tool unavailable: {err_msg}"  # noqa: B023

    google_serper_tool = Tool(
        name="google_serper",
        func=_serper_stub,
        description="Search the web using the Serper API",
    )

__all__ = ["google_serper_tool"]
