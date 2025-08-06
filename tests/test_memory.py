import logging
import os

from agents.memory import get_memory_if_enabled


def test_memory_persists(tmp_path, monkeypatch):
    db_path = tmp_path / "mem.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    mem1 = get_memory_if_enabled(True, session_id="sess1")
    mem1.chat_memory.add_user_message("hi")
    mem1.chat_memory.add_ai_message("hello")

    # recreate memory to ensure messages loaded from DB
    mem2 = get_memory_if_enabled(True, session_id="sess1")
    history = mem2.chat_memory.messages
    assert len(history) == 2
    assert history[0].content == "hi"
    assert history[1].content == "hello"


def test_memory_falls_back_without_db(monkeypatch):
    """If DATABASE_URL is missing, memory should still be usable but not persist."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    mem1 = get_memory_if_enabled(True, session_id="sess2")
    mem1.chat_memory.add_user_message("hi")
    mem1.chat_memory.add_ai_message("there")

    mem2 = get_memory_if_enabled(True, session_id="sess2")
    assert mem2.chat_memory.messages == []


def test_memory_falls_back_without_driver(monkeypatch, caplog):
    """If the database driver is missing, the code should warn and continue."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")

    def fail(*_, **__):  # simulate missing psycopg2
        raise ModuleNotFoundError("No module named 'psycopg2'")

    monkeypatch.setattr("agents.memory.create_engine", fail)
    with caplog.at_level(logging.WARNING):
        mem = get_memory_if_enabled(True, session_id="sess3")
    assert mem.chat_memory.messages == []
    assert "psycopg2" in caplog.text
