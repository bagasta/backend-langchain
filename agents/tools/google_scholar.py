"""Google Scholar search tool."""
from langchain.agents import Tool

try:  # pragma: no cover - requires SerpAPI
    from langchain_community.tools.google_scholar import GoogleScholarQueryRun

    google_scholar_tool = GoogleScholarQueryRun()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _scholar_stub(query: str) -> str:
        return f"Google Scholar tool unavailable: {err_msg}"  # noqa: B023

    google_scholar_tool = Tool(
        name="google_scholar",
        func=_scholar_stub,
        description="Search academic papers with Google Scholar",
    )

__all__ = ["google_scholar_tool"]
