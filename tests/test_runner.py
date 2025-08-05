import pytest
from config.schema import AgentConfig
from agents.runner import run_custom_agent


def test_run_custom_agent_raises_on_iteration_limit(monkeypatch):
    class FakeAgent:
        def invoke(self, _):
            return {"output": "Agent stopped due to iteration limit or time limit."}

    monkeypatch.setattr("agents.runner.build_agent", lambda config: FakeAgent())

    config = AgentConfig(model_name="gpt-4", system_message="", tools=[], memory_enabled=False)
    with pytest.raises(ValueError, match="stopped before producing"):
        run_custom_agent(config, "hi")
