# Build agent from config
# agents/builder.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, AgentType
from langchain.agents.format_scratchpad import format_log_to_messages
from langchain.agents.output_parsers import ReActSingleInputOutputParser
from langchain.tools.render import render_text_description
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
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

    # 3. Build ReAct-style prompt including required tool placeholders
    system_template = (
        "{system_message}\n\n"
        "You can use the following tools:\n{tools}\n\n"
        "Use the following format:\n"
        "Question: the input question you must answer\n"
        "Thought: you should always think about what to do\n"
        "Action: the action to take, should be one of [{tool_names}]\n"
        "Action Input: the input to the action\n"
        "Observation: the result of the action\n"
        "... (this Thought/Action/Action Input/Observation can repeat N times)\n"
        "Thought: I now know the final answer\n"
        "Final Answer: the final answer to the original input question"
    )
    prompt_messages = [
        ("system", system_template),
        ("human", "Question: {input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ]
    if config.memory_enabled:
        prompt_messages.insert(1, MessagesPlaceholder("chat_history"))
    prompt = ChatPromptTemplate.from_messages(prompt_messages).partial(
        system_message=config.system_message
    )

    # 4. Support both chat ReAct variants from LangChain
    supported_agent_types = {
        AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION,
        AgentType.CHAT_ZERO_SHOT_REACT_DESCRIPTION,
    }
    if config.agent_type not in supported_agent_types:
        raise ValueError(
            f"Unsupported agent type: {config.agent_type.value}"
        )

    # 5. Inject tool metadata and build ReAct agent that keeps scratchpad as messages
    missing = {"tools", "tool_names", "agent_scratchpad"}.difference(
        prompt.input_variables + list(prompt.partial_variables)
    )
    if missing:
        raise ValueError(f"Prompt missing required variables: {missing}")

    prompt = prompt.partial(
        tools=render_text_description(list(tools)),
        tool_names=", ".join([t.name for t in tools]),
    )

    agent_chain = (
        RunnablePassthrough.assign(
            agent_scratchpad=lambda x: format_log_to_messages(x["intermediate_steps"])
        )
        | prompt
        | llm
        | ReActSingleInputOutputParser()
    )

    executor = AgentExecutor(
        agent=agent_chain,
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
