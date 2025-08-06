import pytest
from langchain.agents import AgentType
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

    def fake_chat_openai(**kwargs):
        return object()

    class DummyConversationalAgent:
        @staticmethod
        def from_llm_and_tools(llm, tools, prefix):
            captured["prefix"] = prefix
            return "agent"

    def fake_from_agent_and_tools(agent, tools, **kwargs):
        captured["executor_kwargs"] = kwargs
        captured["agent"] = agent
        return "executor"

    monkeypatch.setattr("agents.builder.ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr("agents.builder.ConversationalAgent", DummyConversationalAgent)
    monkeypatch.setattr(
        "agents.builder.AgentExecutor.from_agent_and_tools", fake_from_agent_and_tools
    )

    config = AgentConfig(
        model_name="gpt-4",
        system_message="follow these rules",
        tools=[],
        memory_enabled=False,
    )

    agent = build_agent(config)
    assert agent == "executor"
    assert captured["prefix"] == "follow these rules"
    assert captured["executor_kwargs"]["handle_parsing_errors"] is True


def test_build_agent_sets_iteration_limits(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")

    class DummyExecutor:
        def __init__(self, *, max_iterations=None, max_execution_time=None, **kwargs):
            self.max_iterations = max_iterations
            self.max_execution_time = max_execution_time

    monkeypatch.setattr("agents.builder.ChatOpenAI", lambda **_: object())

    class DummyConversationalAgent:
        @staticmethod
        def from_llm_and_tools(llm, tools, prefix):
            return "agent"

    monkeypatch.setattr("agents.builder.ConversationalAgent", DummyConversationalAgent)

    def fake_from_agent_and_tools(agent, tools, **kwargs):
        return DummyExecutor(**kwargs)

    monkeypatch.setattr(
        "agents.builder.AgentExecutor.from_agent_and_tools", fake_from_agent_and_tools
    )

    config = AgentConfig(
        model_name="gpt-4",
        system_message="hi",
        tools=[],
        memory_enabled=False,
        max_iterations=5,
        max_execution_time=30,
    )

    executor = build_agent(config)
    assert executor.max_iterations == 5
    assert executor.max_execution_time == 30


def test_build_agent_rejects_bad_agent_type(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setattr("agents.builder.ChatOpenAI", lambda **_: object())
    with pytest.raises(ValueError, match="Unsupported agent type"):
        build_agent(
            AgentConfig(
                model_name="gpt-4",
                system_message="hi",
                tools=[],
                memory_enabled=False,
                agent_type=AgentType.OPENAI_FUNCTIONS,
            )
        )


def test_build_agent_accepts_conversational_types(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setattr("agents.builder.ChatOpenAI", lambda **_: object())

    class DummyConversationalAgent:
        @staticmethod
        def from_llm_and_tools(llm, tools, prefix):
            return "agent"

    monkeypatch.setattr("agents.builder.ConversationalAgent", DummyConversationalAgent)
    monkeypatch.setattr(
        "agents.builder.AgentExecutor.from_agent_and_tools", lambda **_: "executor"
    )

    config = AgentConfig(
        model_name="gpt-4",
        system_message="hi",
        tools=[],
        memory_enabled=False,
        agent_type=AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION,
    )

    assert build_agent(config) == "executor"
