"""Google Lens search tool."""
from langchain.agents import Tool

try:  # pragma: no cover - requires SerpAPI
    from langchain_community.tools.google_lens import GoogleLensQueryRun

    google_lens_tool = GoogleLensQueryRun()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _lens_stub(query: str) -> str:
        return f"Google Lens tool unavailable: {err_msg}"  # noqa: B023

    google_lens_tool = Tool(
        name="google_lens",
        func=_lens_stub,
        description="Search using Google Lens",
    )

__all__ = ["google_lens_tool"]
