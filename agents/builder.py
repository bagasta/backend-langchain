# Build agent from config
# agents/builder.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, AgentType, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from pydantic import ValidationError
from config.schema import AgentConfig
from agents.tools.registry import get_tools_by_names
from agents.memory import MemoryBackend, get_history_loader

load_dotenv()


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

    # 3. Ensure a supported conversational agent type
    supported_agent_types = {
        AgentType.CONVERSATIONAL_REACT_DESCRIPTION,
        AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION,
    }
    if config.agent_type not in supported_agent_types:
        raise ValueError(
            f"Unsupported agent type: {config.agent_type.value}"
        )

    # 4. Build an agent capable of calling tools multiple times
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", config.system_message),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, tools, prompt)

    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=config.max_iterations,
        max_execution_time=config.max_execution_time,
    )

    # 6. Optionally wrap with message history for memory
    if config.memory_enabled:
        backend = MemoryBackend(config.memory_backend)
        history_loader = get_history_loader(backend)
        executor = RunnableWithMessageHistory(
            executor,
            history_loader,
            input_messages_key="input",
            history_messages_key="chat_history",
            output_messages_key="output",
        )

    return executor
