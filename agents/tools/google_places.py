"""Google Places API tool."""
from langchain.agents import Tool

try:  # pragma: no cover - may require extra package
    from langchain_community.tools.google_places import GooglePlacesTool

    google_places_tool = GooglePlacesTool()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _places_stub(query: str) -> str:
        return f"Google Places tool unavailable: {err_msg}"  # noqa: B023

    google_places_tool = Tool(
        name="google_places",
        func=_places_stub,
        description="Search for places using the Google Places API",
    )

__all__ = ["google_places_tool"]
