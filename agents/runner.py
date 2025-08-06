# Run agent loop
# agents/runner.py

from config.schema import AgentConfig
from agents.builder import build_agent


def run_custom_agent(agent_id: str, config: AgentConfig, message: str) -> str:
    """Build agent from config and execute it on the provided message."""

    agent = build_agent(config)
    payload = {"input": message}
    if config.memory_enabled:
        result = agent.invoke(payload, config={"configurable": {"session_id": agent_id}})
    else:
        result = agent.invoke(payload)
    if isinstance(result, dict):
        result = result.get("output", "")
    if result == "Agent stopped due to iteration limit or time limit.":
        raise ValueError(
            "Agent execution stopped before producing a final answer. "
            "Consider increasing max_iterations or revising the prompt."
        )
    return result
