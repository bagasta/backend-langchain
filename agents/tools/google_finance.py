"""Google Finance query tool."""
from langchain.agents import Tool

try:  # pragma: no cover - requires SerpAPI
    from langchain_community.tools.google_finance import GoogleFinanceQueryRun

    google_finance_tool = GoogleFinanceQueryRun()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _finance_stub(query: str) -> str:
        return f"Google Finance tool unavailable: {err_msg}"  # noqa: B023

    google_finance_tool = Tool(
        name="google_finance",
        func=_finance_stub,
        description="Look up financial data via Google Finance",
    )

__all__ = ["google_finance_tool"]
