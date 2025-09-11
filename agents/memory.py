"""Chat history backends for agent memory.

This module exposes utilities for constructing chat message history stores
compatible with LangChain's ``RunnableWithMessageHistory`` wrapper.  It
provides multiple backends – in-memory, SQL and file based – so each agent can
retain context across requests using the mechanism best suited for the
deployment environment.
"""

from __future__ import annotations

import logging
import json
import os
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_community.chat_message_histories import (
    ChatMessageHistory,
    FileChatMessageHistory,
    SQLChatMessageHistory,
)
from sqlalchemy import create_engine, text


class MemoryBackend(str, Enum):
    """Supported chat history backends."""

    IN_MEMORY = "in_memory"
    SQL = "sql"
    FILE = "file"


def _memory_table_for_session(session_id: str) -> str | None:
    """Return per-agent memory table name from session id, or None for default.

    Expected session_id format: "<user_id>:<agent_id>" (digits), yielding
    table name: memory_<user_id><agent_id> (e.g., memory_39).
    """
    try:
        # Allow composite session_id in the form "<user>:<agent>|<chat>".
        # Use the prefix before '|' to derive the table name.
        prefix = session_id.split("|", 1)[0]
        if ":" not in prefix:
            return None
        left, right = prefix.split(":", 1)
        uid = "".join(ch for ch in str(left) if ch.isdigit())
        aid = "".join(ch for ch in str(right) if ch.isdigit())
        if uid and aid:
            return f"memory_{uid}{aid}"
        return None
    except Exception:
        return None


def _load_sql_history(session_id: str) -> BaseChatMessageHistory:
    """Return an SQL-backed chat history or fall back to in-memory storage."""

    # Prefer dedicated memory database if provided; fall back to app DB
    db_url = os.getenv("MEMORY_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        logging.warning(
            "MEMORY_DATABASE_URL/DATABASE_URL not set; using ephemeral in-memory conversation store",
        )
        return ChatMessageHistory()

    try:
        timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))
        engine = create_engine(
            db_url,
            connect_args={"connect_timeout": timeout},
            pool_pre_ping=True,
        )
        # Fail fast if DB is unreachable
        try:
            with engine.connect() as conn:
                pass
        except Exception:
            logging.warning("DB unreachable for SQL memory; using in-memory history instead")
            return ChatMessageHistory()
        # Prefer per-agent table when session encodes user:agent
        table_name = _memory_table_for_session(session_id)
        if table_name:
            # Ensure table exists (id serial, session_id varchar(255), message text)
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f"""
                        CREATE TABLE IF NOT EXISTS public."{table_name}" (
                           id serial PRIMARY KEY,
                           session_id character varying(255) NOT NULL,
                           message text NOT NULL
                        )
                        """
                    ))
                    conn.execute(text(
                        f'CREATE INDEX IF NOT EXISTS "{table_name}_session_idx" ON public."{table_name}" (session_id)'
                    ))
                    try:
                        conn.execute(text(
                            f'ALTER TABLE public."{table_name}" ALTER COLUMN message TYPE text USING message::text'
                        ))
                    except Exception:
                        pass
                    # Normalize older rows missing the `data` wrapper to the LC schema
                    try:
                        conn.execute(text(
                            f"""
                            UPDATE public."{table_name}"
                            SET message = (
                                json_build_object(
                                    'type', (message::jsonb->>'type'),
                                    'data', jsonb_build_object(
                                        'content', (message::jsonb->>'content'),
                                        'additional_kwargs', COALESCE(message::jsonb->'additional_kwargs','{{}}'::jsonb),
                                        'response_metadata', COALESCE(message::jsonb->'response_metadata','{{}}'::jsonb)
                                    )
                                )::text
                            )
                            WHERE (message::jsonb ? 'content') AND NOT (message::jsonb ? 'data');
                            """
                        ))
                    except Exception:
                        pass
            except Exception:
                # Non-fatal; SQLChatMessageHistory will attempt to create its own table
                pass
            try:
                print(f"[MEM] Using SQL memory table={table_name} for session={session_id}")
            except Exception:
                pass
            # Persist messages keyed by chat-id (suffix after '|')
            chat_id = session_id.split("|", 1)[1] if "|" in session_id else session_id
            return SQLChatMessageHistory(session_id=chat_id, connection=engine, table_name=table_name)
        # Fallback default table using provided session_id as-is
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


# A thin wrapper that limits messages returned from a history store
class LimitingHistory(BaseChatMessageHistory):
    def __init__(self, inner: BaseChatMessageHistory, limit: Optional[int]):
        self.inner = inner
        self.limit = max(0, int(limit)) if (limit is not None) else None

    def add_message(self, message):  # type: ignore[override]
        return self.inner.add_message(message)

    def add_messages(self, messages):  # type: ignore[override]
        try:
            return self.inner.add_messages(messages)  # type: ignore[attr-defined]
        except Exception:
            for m in messages:
                self.inner.add_message(m)

    def clear(self):  # type: ignore[override]
        return self.inner.clear()

    def get_messages(self):  # type: ignore[override]
        try:
            msgs = list(self.inner.get_messages())  # type: ignore[attr-defined]
        except Exception:
            try:
                msgs = list(getattr(self.inner, "messages", []))
            except Exception:
                msgs = []
        if self.limit is None:
            return msgs
        if self.limit <= 0:
            return []
        if len(msgs) <= self.limit:
            return msgs
        return msgs[-self.limit :]

    # Provide `.messages` property for components that access it directly
    @property
    def messages(self):  # type: ignore[override]
        return self.get_messages()


# ---- Lightweight helper for manual persistence (fallback) ----
_ENGINE_CACHE = None


def _get_engine():
    global _ENGINE_CACHE
    if _ENGINE_CACHE is not None:
        return _ENGINE_CACHE
    db_url = os.getenv("MEMORY_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        return None
    try:
        timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))
        _ENGINE_CACHE = create_engine(
            db_url,
            connect_args={"connect_timeout": timeout},
            pool_pre_ping=True,
        )
        return _ENGINE_CACHE
    except Exception:
        return None


def persist_conversation(session_id: str, human_text: str, ai_text: str, chat_session_id: str | None = None) -> None:
    """Best-effort manual append of a turn to the per-agent memory table.

    This is a safety net in case the LangChain history wrapper does not write.
    """
    table = _memory_table_for_session(session_id)
    if not table:
        return
    eng = _get_engine()
    if eng is None:
        return
    try:
        with eng.begin() as conn:
            conn.execute(text(
                f"""
                CREATE TABLE IF NOT EXISTS public."{table}" (
                   id serial PRIMARY KEY,
                   session_id character varying(255) NOT NULL,
                   message text NOT NULL
                )
                """
            ))
            conn.execute(text(
                f'CREATE INDEX IF NOT EXISTS "{table}_session_idx" ON public."{table}" (session_id)'
            ))
            try:
                conn.execute(text(
                    f'ALTER TABLE public."{table}" ALTER COLUMN message TYPE text USING message::text'
                ))
            except Exception:
                pass
            # Insert human and AI messages in LangChain-compatible format,
            # but only if they are not already present (avoid duplicates when LC already wrote)
            row_sid = chat_session_id or session_id
            # Check existing human
            human_exists = False
            try:
                res = conn.execute(text(
                    f"""
                    SELECT 1 FROM public."{table}"
                    WHERE session_id = :sid
                      AND (message::jsonb->>'type') = 'human'
                      AND (message::jsonb->'data'->>'content') = :content
                    LIMIT 1
                    """
                ), {"sid": row_sid, "content": human_text})
                human_exists = res.first() is not None
            except Exception:
                human_exists = False

            if not human_exists:
                conn.execute(
                    text(f'INSERT INTO public."{table}" (session_id, message) VALUES (:sid, :msg)'),
                    {
                        "sid": row_sid,
                        "msg": json.dumps({
                            "type": "human",
                            "data": {
                                "content": human_text,
                                "additional_kwargs": {},
                                "response_metadata": {},
                            },
                        }),
                    },
                )

            # Check existing ai
            ai_exists = False
            try:
                res = conn.execute(text(
                    f"""
                    SELECT 1 FROM public."{table}"
                    WHERE session_id = :sid
                      AND (message::jsonb->>'type') = 'ai'
                      AND (message::jsonb->'data'->>'content') = :content
                    LIMIT 1
                    """
                ), {"sid": row_sid, "content": ai_text})
                ai_exists = res.first() is not None
            except Exception:
                ai_exists = False

            if not ai_exists:
                conn.execute(
                    text(f'INSERT INTO public."{table}" (session_id, message) VALUES (:sid, :msg)'),
                    {
                        "sid": row_sid,
                        "msg": json.dumps({
                            "type": "ai",
                            "data": {
                                "content": ai_text,
                                "additional_kwargs": {},
                                "response_metadata": {},
                            },
                        }),
                    },
                )
            else:
                try:
                    print(f"[MEM] Fallback insert skipped; rows already exist for sid={row_sid}")
                except Exception:
                    pass
        try:
            print(f"[MEM] Fallback persisted conversation to table={table}")
        except Exception:
            pass
    except Exception as e:
        try:
            print(f"[MEM] Fallback persist failed for table={table}: {e}")
        except Exception:
            pass


def get_history_loader(backend: MemoryBackend, limit: Optional[int] = None) -> Callable[[str], BaseChatMessageHistory]:
    """Return a factory that builds chat histories for ``RunnableWithMessageHistory``.

    The returned callable accepts a ``session_id`` and yields an appropriate
    ``BaseChatMessageHistory`` instance.
    """

    if backend == MemoryBackend.SQL:
        def _loader(session_id: str) -> BaseChatMessageHistory:
            return LimitingHistory(_load_sql_history(session_id), limit)
        return _loader
    if backend == MemoryBackend.FILE:
        def _loader_file(session_id: str) -> BaseChatMessageHistory:
            return LimitingHistory(_load_file_history(session_id), limit)
        return _loader_file
    return lambda _session_id: LimitingHistory(ChatMessageHistory(), limit)
