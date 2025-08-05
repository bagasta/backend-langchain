# Run agent loop
# agents/runner.py

from config.schema import AgentConfig
from agents.builder import build_agent


def run_custom_agent(config: AgentConfig, message: str) -> str:
    """Build agent from config and execute it on the provided message."""
    agent = build_agent(config)
    result = agent.invoke({"input": message})
    if isinstance(result, dict):
        return result.get("output", "")
    return result
