# Build agent from config
# agents/builder.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
from pydantic import ValidationError
from config.schema import AgentConfig
from agents.tools.registry import get_tools_by_names
from agents.memory import get_memory_if_enabled

load_dotenv()


def _resolve_agent_type(agent_type_str: str) -> AgentType:
    """Map a string to a LangChain AgentType."""
    if not agent_type_str:
        return AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION
    try:
        return AgentType(agent_type_str)
    except ValueError:
        try:
            return AgentType[agent_type_str.upper()]
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Unsupported agent type: {agent_type_str}") from exc


def build_agent(config: AgentConfig):
    """Construct a LangChain agent executor from the provided configuration."""

    # 1. Initialize LLM with provided or environment API key
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OpenAI API key not provided. Set OPENAI_API_KEY env var or include openai_api_key in config."
        )
    try:
        llm = ChatOpenAI(
            model_name=config.model_name,
            temperature=0,
            openai_api_key=api_key,
        )
    except ValidationError as exc:
        raise ValueError(
            "OpenAI API key not provided. Set OPENAI_API_KEY env var or include openai_api_key in config."
        ) from exc

    # 2. Gather tools from registry
    tools = get_tools_by_names(config.tools)

    # 3. Optional memory
    memory = get_memory_if_enabled(config.memory_enabled)

    # 4. Resolve agent type and pass system message
    agent_type = _resolve_agent_type(config.agent_type)
    agent_kwargs = {}
    if config.system_message:
        agent_kwargs["system_message"] = config.system_message

    # 5. Initialize agent executor capable of multiple tool invocations
    executor = initialize_agent(
        tools=tools,
        llm=llm,
        agent=agent_type,
        agent_kwargs=agent_kwargs,
        verbose=True,
        memory=memory,
        handle_parsing_errors=True,
        max_iterations=config.max_iterations,
        max_execution_time=config.max_execution_time,
    )

    return executor
