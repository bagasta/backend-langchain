"""Stub Google search tool."""

from langchain.agents import Tool


def _search(query: str) -> str:
    """Return stub search results for the given query."""
    # In production, integrate with a real search API like Google or SerpAPI
    return f"Stub search results for: {query}"


google_search_tool = Tool(
    name="google_search",
    func=_search,
    description="Search the web for up-to-date information"
)

__all__ = ["google_search_tool"]
