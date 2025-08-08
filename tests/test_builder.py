import pytest
from langchain.agents import AgentType, Tool
from langchain_core.messages import AIMessage
from langchain_community.chat_models.fake import FakeMessagesListChatModel

from config.schema import AgentConfig
from agents.builder import build_agent


def test_build_agent_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = AgentConfig(
        model_name="gpt-4o-mini",
        system_message="test",
        tools=[],
        memory_enabled=False,
    )
    with pytest.raises(ValueError, match="OpenAI API key"):
        build_agent(config)


def test_build_agent_applies_system_message(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    captured = {}

    def fake_create_agent(llm, tools, prompt):
        captured["system"] = prompt.messages[0].prompt.template
        return "agent"

    class DummyExecutor:
        def __init__(self, *, agent, tools, **kwargs):
            captured["executor_kwargs"] = kwargs

    monkeypatch.setattr("agents.builder.ChatOpenAI", lambda **_: object())
    monkeypatch.setattr("agents.builder.create_tool_calling_agent", fake_create_agent)
    monkeypatch.setattr("agents.builder.AgentExecutor", DummyExecutor)

    config = AgentConfig(
        model_name="gpt-4o-mini",
        system_message="follow these rules",
        tools=[],
        memory_enabled=False,
    )

    build_agent(config)
    assert captured["system"] == "follow these rules"
    assert captured["executor_kwargs"]["handle_parsing_errors"] is True


def test_build_agent_sets_iteration_limits(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")

    class DummyExecutor:
        def __init__(self, *, agent, tools, max_iterations=None, max_execution_time=None, **kwargs):
            self.max_iterations = max_iterations
            self.max_execution_time = max_execution_time

    monkeypatch.setattr("agents.builder.ChatOpenAI", lambda **_: object())
    monkeypatch.setattr("agents.builder.create_tool_calling_agent", lambda *_, **__: "agent")
    monkeypatch.setattr("agents.builder.AgentExecutor", DummyExecutor)

    config = AgentConfig(
        model_name="gpt-4o-mini",
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
                model_name="gpt-4o-mini",
                system_message="hi",
                tools=[],
                memory_enabled=False,
                agent_type=AgentType.OPENAI_FUNCTIONS,
            )
        )


def test_agent_can_use_multiple_tools(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")

    calls = []

    def google_func(q):
        calls.append(("google_search", q))
        return "result"

    def calc_func(expr):
        calls.append(("calc", expr))
        return "4"

    google_tool = Tool(name="google_search", func=google_func, description="search")
    calc_tool = Tool(name="calculator", func=calc_func, description="calc")
    monkeypatch.setattr(
        "agents.tools.registry.TOOL_REGISTRY",
        {"google_search": google_tool, "calc": calc_tool},
    )

    class DummyLLM(FakeMessagesListChatModel):
        def bind_tools(self, tools):
            return self

    llm = DummyLLM(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "1", "name": "google_search", "args": {"query": "python"}}]),
            AIMessage(content="", tool_calls=[{"id": "2", "name": "calculator", "args": {"expression": "2+2"}}]),
            AIMessage(content="final"),
        ]
    )

    monkeypatch.setattr("agents.builder.ChatOpenAI", lambda **_: llm)

    config = AgentConfig(
        model_name="gpt-4o-mini",
        system_message="system",
        tools=["google_search", "calc"],
        memory_enabled=False,
    )

    executor = build_agent(config)
    result = executor.invoke({"input": "question", "chat_history": []})
    assert result["output"] == "final"
    assert calls == [("google_search", "python"), ("calc", "2+2")]
