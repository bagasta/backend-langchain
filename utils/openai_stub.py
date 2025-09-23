"""Fallback stub when langchain-openai is unavailable.

The real service should install ``langchain-openai``.  This stub simply raises a
clear error if the class is instantiated so environments without the optional
dependency fail fast with a helpful message.
"""

from __future__ import annotations


class ChatOpenAI:  # pragma: no cover - exercised when dependency missing at runtime
    def __init__(self, *_, **__):
        raise ImportError(
            "langchain-openai is not installed. Install it with `pip install langchain-openai` "
            "to enable ChatOpenAI integrations."
        )

    def invoke(self, *_, **__):
        raise RuntimeError("ChatOpenAI is unavailable because langchain-openai is not installed.")
