# Run agent loop
# agents/runner.py

from config.schema import AgentConfig
from agents.builder import build_agent
import json


def run_custom_agent(agent_id: str, config: AgentConfig, message: str) -> str:
    """Build agent from config and execute it on the provided message."""

    agent = build_agent(config)
    payload = {"input": message}
    try:
        if config.memory_enabled:
            result = agent.invoke(payload, config={"configurable": {"session_id": agent_id}})
        else:
            payload["chat_history"] = []
            result = agent.invoke(payload)
    except Exception as exc:
        # Convert unexpected agent errors into a user-visible message instead of HTTP 500s
        raise ValueError(f"Agent execution failed: {exc}") from exc

    # Normalize various return types to a safe string
    if isinstance(result, dict):
        result = result.get("output") or json.dumps(result, default=str)
    elif hasattr(result, "content"):
        try:
            result = result.content  # e.g., AIMessage
        except Exception:
            result = str(result)
    elif not isinstance(result, str):
        try:
            result = json.dumps(result, default=str)
        except Exception:
            result = str(result)

    if result == "Agent stopped due to iteration limit or time limit.":
        raise ValueError(
            "Agent execution stopped before producing a final answer. "
            "Consider increasing max_iterations or revising the prompt."
        )
    return result
