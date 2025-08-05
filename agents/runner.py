# Run agent loop
# agents/runner.py

from config.schema import AgentConfig
from agents.builder import build_agent


def run_custom_agent(config: AgentConfig, message: str) -> str:
    """Build agent from config and execute it on the provided message."""
    agent = build_agent(config)
    result = agent.invoke({"input": message})
    if isinstance(result, dict):
        result = result.get("output", "")
    if result == "Agent stopped due to iteration limit or time limit.":
        raise ValueError(
            "Agent execution stopped before producing a final answer. "
            "Consider increasing max_iterations or revising the prompt."
        )
    return result
