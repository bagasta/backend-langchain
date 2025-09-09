"""Chat history backends for agent memory.

This module exposes utilities for constructing chat message history stores
compatible with LangChain's ``RunnableWithMessageHistory`` wrapper.  It
provides multiple backends – in-memory, SQL and file based – so each agent can
retain context across requests using the mechanism best suited for the
deployment environment.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Callable

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_community.chat_message_histories import (
    ChatMessageHistory,
    FileChatMessageHistory,
    SQLChatMessageHistory,
)
from sqlalchemy import create_engine


class MemoryBackend(str, Enum):
    """Supported chat history backends."""

    IN_MEMORY = "in_memory"
    SQL = "sql"
    FILE = "file"


def _load_sql_history(session_id: str) -> BaseChatMessageHistory:
    """Return an SQL-backed chat history or fall back to in-memory storage."""

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logging.warning(
            "DATABASE_URL not set; falling back to ephemeral in-memory conversation store",
        )
        return ChatMessageHistory()

    try:
        timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))
        engine = create_engine(db_url, connect_args={"connect_timeout": timeout})
        # Fail fast if DB is unreachable
        try:
            with engine.connect() as conn:
                pass
        except Exception:
            logging.warning("DB unreachable for SQL memory; using in-memory history instead")
            return ChatMessageHistory()
        return SQLChatMessageHistory(session_id=session_id, connection=engine)
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional deps
        logging.warning("%s; falling back to ephemeral in-memory conversation store", exc)
        return ChatMessageHistory()


def _load_file_history(session_id: str) -> BaseChatMessageHistory:
    """Return a file-based chat history store."""

    directory = Path(os.getenv("MEMORY_DIR", "."))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    return FileChatMessageHistory(str(path))


def get_history_loader(backend: MemoryBackend) -> Callable[[str], BaseChatMessageHistory]:
    """Return a factory that builds chat histories for ``RunnableWithMessageHistory``.

    The returned callable accepts a ``session_id`` and yields an appropriate
    ``BaseChatMessageHistory`` instance.
    """

    if backend == MemoryBackend.SQL:
        return _load_sql_history
    if backend == MemoryBackend.FILE:
        return _load_file_history
    return lambda _session_id: ChatMessageHistory()
