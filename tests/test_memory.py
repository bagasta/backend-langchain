import logging
import os

from agents.memory import MemoryBackend, get_history_loader


def test_memory_persists(tmp_path, monkeypatch):
    db_path = tmp_path / "mem.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    loader = get_history_loader(MemoryBackend.SQL)
    mem1 = loader("sess1")
    mem1.add_user_message("hi")
    mem1.add_ai_message("hello")

    # recreate history to ensure messages loaded from DB
    mem2 = loader("sess1")
    history = mem2.messages
    assert len(history) == 2
    assert history[0].content == "hi"
    assert history[1].content == "hello"


def test_memory_falls_back_without_db(monkeypatch):
    """If DATABASE_URL is missing, memory should still be usable but not persist."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    loader = get_history_loader(MemoryBackend.SQL)
    mem1 = loader("sess2")
    mem1.add_user_message("hi")
    mem1.add_ai_message("there")

    mem2 = loader("sess2")
    assert mem2.messages == []


def test_memory_falls_back_without_driver(monkeypatch, caplog):
    """If the database driver is missing, the code should warn and continue."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")

    def fail(*_, **__):  # simulate missing psycopg2
        raise ModuleNotFoundError("No module named 'psycopg2'")

    monkeypatch.setattr("agents.memory.create_engine", fail)
    loader = get_history_loader(MemoryBackend.SQL)
    with caplog.at_level(logging.WARNING):
        mem = loader("sess3")
    assert mem.messages == []
    assert "psycopg2" in caplog.text
