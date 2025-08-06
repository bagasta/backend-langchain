"""Web search tool using OpenAI gpt-4o-search-preview."""

from __future__ import annotations

import os
import requests
from langchain.agents import Tool


SYSTEM_PROMPT = (
    "You are an AI shopping assistant who specializes in finding the latest and "
    "most relevant products. Please provide me with ONLY the product name"
)


def _search(query: str) -> str:
    """Call OpenAI search model and return the product name result."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "error: OpenAI API key not set"

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "gpt-4o-search-preview",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # pragma: no cover - network or parsing errors
        return f"error: {exc}"


websearch_tool = Tool(
    name="websearch",
    func=_search,
    description="Search the web for product information via OpenAI",
)

__all__ = ["websearch_tool"]
