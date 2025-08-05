# Run agent loop
# agents/runner.py

from config.schema import AgentConfig
from agents.builder import build_agent

def run_custom_agent(config: AgentConfig, message: str) -> str:
    """
    Build a LangChain agent from the given AgentConfig and run it on the input message,
    returning the agent’s response.
    """
    agent = build_agent(config)
    return agent.run(message)
