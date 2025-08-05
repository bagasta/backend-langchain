import pytest
from config.schema import AgentConfig
from agents.builder import build_agent
from langchain.agents import AgentType


def test_build_agent_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = AgentConfig(
        model_name="gpt-4",
        system_message="test",
        tools=[],
        memory_enabled=False,
    )
    with pytest.raises(ValueError, match="OpenAI API key"):
        build_agent(config)


def test_build_agent_applies_system_message(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    captured = {}

    class DummyLLM:
        pass

    def fake_chat_openai(**kwargs):
        return DummyLLM()

    def fake_initialize_agent(**kwargs):
        captured.update(kwargs)
        return "agent"

    monkeypatch.setattr("agents.builder.ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr("agents.builder.initialize_agent", fake_initialize_agent)

    config = AgentConfig(
        model_name="gpt-4",
        system_message="follow these rules",
        tools=[],
        memory_enabled=False,
    )

    agent = build_agent(config)
    assert agent == "agent"
    assert captured["agent"] == AgentType.CHAT_ZERO_SHOT_REACT_DESCRIPTION
    assert captured["agent_kwargs"]["system_message"] == "follow these rules"
