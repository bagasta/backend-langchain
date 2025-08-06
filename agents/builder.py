# Build agent from config
# agents/builder.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, AgentType, create_react_agent
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

    # 3. Build custom ReAct prompt including required tool placeholders
    system_template = (
        "{system_message}\n\n"
        "You can use the following tools:\n{tools}\n\n"
        "When deciding on actions, use the tool name exactly as in this list: {tool_names}."
    )
    prompt_messages = [
        ("system", system_template),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ]
    prompt = ChatPromptTemplate.from_messages(prompt_messages).partial(
        system_message=config.system_message
    )

    # 4. Only the default chat-conversational ReAct agent is supported
    if config.agent_type != AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION:
        raise ValueError(
            f"Unsupported agent type: {config.agent_type.value}"
        )

    # 5. Create ReAct agent and executor
    agent = create_react_agent(llm, tools, prompt)
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
