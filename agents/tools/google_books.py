"""Google Books search tool."""
from langchain.agents import Tool

try:  # pragma: no cover - external API
    from langchain_community.tools.google_books import GoogleBooksQueryRun

    google_books_tool = GoogleBooksQueryRun()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _books_stub(query: str) -> str:
        return f"Google Books tool unavailable: {err_msg}"  # noqa: B023

    google_books_tool = Tool(
        name="google_books",
        func=_books_stub,
        description="Search books using the Google Books API",
    )

__all__ = ["google_books_tool"]
