import pytest
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
