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
