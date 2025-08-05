# Build agent from config
# agents/builder.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
from langchain.schema import SystemMessage
from pydantic import ValidationError
from config.schema import AgentConfig
from agents.tools.registry import get_tools_by_names
from agents.memory import get_memory_if_enabled

load_dotenv()

def build_agent(config: AgentConfig):
    """
    Membangun LangChain agent berdasarkan AgentConfig:
    - model_name (mis. "gpt-4")
    - system_message (prompt awal system)
    - tools (list nama tool)
    - memory_enabled (True/False)
    """
    # 1. Inisiasi LLM dengan API key yang diberikan atau dari environment
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

    # 2. Ambil tool dari registry sesuai nama
    tools = get_tools_by_names(config.tools)

    # 3. Siapkan memory jika diaktifkan
    memory = get_memory_if_enabled(config.memory_enabled)

    # 4. Inisialisasi agent berbasis Chat agar system_message diterapkan
    agent_kwargs = {}
    if config.system_message:
        agent_kwargs["system_message"] = SystemMessage(content=config.system_message)

    agent_type = AgentType.CHAT_ZERO_SHOT_REACT_DESCRIPTION
    if memory:
        agent_type = AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION

    agent = initialize_agent(
        tools=tools,
        llm=llm,
        agent=agent_type,
        verbose=True,
        memory=memory,
        agent_kwargs=agent_kwargs or None,
    )

    return agent
