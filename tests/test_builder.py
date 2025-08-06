import pytest
from config.schema import AgentConfig
import pytest
from langchain.agents import AgentType
from langchain_core.prompts import ChatPromptTemplate
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

    def fake_create_react_agent(llm, tools, prompt, agent_type):
        captured["prompt"] = prompt
        captured["agent_type"] = agent_type
        return "agent"

    def fake_agent_executor(agent, tools, **kwargs):
        captured["executor_kwargs"] = kwargs
        captured["agent_passed"] = agent
        return "executor"

    monkeypatch.setattr("agents.builder.ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr("agents.builder.create_react_agent", fake_create_react_agent)
    monkeypatch.setattr("agents.builder.AgentExecutor", fake_agent_executor)

    config = AgentConfig(
        model_name="gpt-4",
        system_message="follow these rules",
        tools=[],
        memory_enabled=False,
        agent_type="openai-functions",
    )

    agent = build_agent(config)
    assert agent == "executor"
    assert captured["agent_type"] == AgentType.OPENAI_FUNCTIONS
    assert isinstance(captured["prompt"], ChatPromptTemplate)
    assert captured["executor_kwargs"]["handle_parsing_errors"] is True


def test_build_agent_sets_iteration_limits(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")

    class DummyExecutor:
        def __init__(self, *, max_iterations=None, max_execution_time=None, **kwargs):
            self.max_iterations = max_iterations
            self.max_execution_time = max_execution_time

    monkeypatch.setattr("agents.builder.ChatOpenAI", lambda **_: object())

    def fake_create_react_agent(llm, tools, prompt, agent_type):
        return "agent"

    def fake_agent_executor(agent, tools, **kwargs):
        return DummyExecutor(**kwargs)

    monkeypatch.setattr("agents.builder.create_react_agent", fake_create_react_agent)
    monkeypatch.setattr("agents.builder.AgentExecutor", fake_agent_executor)

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
                agent_type="unknown-agent",
            )
        )
