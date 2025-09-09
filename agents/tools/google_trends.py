"""Google Trends query tool."""
from langchain.agents import Tool

try:  # pragma: no cover - external dependency
    from langchain_community.tools.google_trends import GoogleTrendsQueryRun

    google_trends_tool = GoogleTrendsQueryRun()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _trends_stub(query: str) -> str:
        return f"Google Trends tool unavailable: {err_msg}"  # noqa: B023

    google_trends_tool = Tool(
        name="google_trends",
        func=_trends_stub,
        description="Query Google Trends data",
    )

__all__ = ["google_trends_tool"]
