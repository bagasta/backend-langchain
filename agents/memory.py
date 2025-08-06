"""Memory utilities for agents.

This module now persists conversation history to the project database so that
each agent retains context across runs. It relies on LangChain's
``SQLChatMessageHistory`` which automatically creates the required SQL table
when pointed at a valid database URL.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from langchain.memory import ConversationBufferMemory
from langchain.memory.chat_memory import BaseChatMemory
from langchain_community.chat_message_histories import SQLChatMessageHistory
from sqlalchemy import create_engine


def get_memory_if_enabled(enabled: bool, session_id: str | None = None) -> Optional[BaseChatMemory]:
    """Return a persistent ``ConversationBufferMemory`` if enabled.

    Parameters
    ----------
    enabled: bool
        Flag indicating whether memory should be activated.
    session_id: str | None
        Identifier used to associate stored messages with a particular agent
        instance. Required when ``enabled`` is ``True`` to ensure messages are
        written to the correct conversation.
    """

    if not enabled:
        return None

    if not session_id:
        raise ValueError("session_id is required when memory is enabled")

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        engine = create_engine(db_url)
        chat_history = SQLChatMessageHistory(session_id=session_id, connection=engine)
        return ConversationBufferMemory(
            memory_key="chat_history", chat_memory=chat_history
        )

    logging.warning(
        "DATABASE_URL not set; falling back to ephemeral in-memory conversation store",
    )
    return ConversationBufferMemory(memory_key="chat_history")
