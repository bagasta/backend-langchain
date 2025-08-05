import pytest
from langchain.prompts import MessagesPlaceholder
from config.schema import AgentConfig
from agents.builder import build_agent


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

    def fake_create_react_agent(llm, tools, prompt):
        captured["prompt"] = prompt
        return "agent"

    def fake_agent_executor(agent, tools, verbose, memory=None):
        captured["memory"] = memory
        captured["agent"] = agent
        return "executor"

    monkeypatch.setattr("agents.builder.ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr("agents.builder.create_react_agent", fake_create_react_agent)
    monkeypatch.setattr("agents.builder.AgentExecutor", fake_agent_executor)

    config = AgentConfig(
        model_name="gpt-4",
        system_message="follow these rules",
        tools=[],
        memory_enabled=False,
    )

    agent = build_agent(config)
    assert agent == "executor"
    prompt_messages = captured["prompt"].messages
    assert prompt_messages[0].prompt.template == "follow these rules"
    assert isinstance(prompt_messages[-1], MessagesPlaceholder)
