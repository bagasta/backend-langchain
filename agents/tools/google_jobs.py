"""Google Jobs search tool."""
from langchain.agents import Tool

try:  # pragma: no cover - requires SerpAPI
    from langchain_community.tools.google_jobs import GoogleJobsQueryRun

    google_jobs_tool = GoogleJobsQueryRun()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _jobs_stub(query: str) -> str:
        return f"Google Jobs tool unavailable: {err_msg}"  # noqa: B023

    google_jobs_tool = Tool(
        name="google_jobs",
        func=_jobs_stub,
        description="Search job listings with Google Jobs",
    )

__all__ = ["google_jobs_tool"]
