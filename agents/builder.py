# Build agent from config
# agents/builder.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_react_agent, AgentExecutor
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
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

    # 4. Bangun prompt dengan system message, optional memory, dan scratchpad
    system_message = config.system_message or "You are a helpful assistant."
    system_template = (
        f"{system_message}\n\n"
        "You have access to the following tools:\n{tools}\n\n"
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

    messages = [("system", system_template)]
    if memory:
        messages.append(MessagesPlaceholder("chat_history"))
    messages.extend([
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    prompt = ChatPromptTemplate.from_messages(messages)

    # 5. Bangun agent ReAct dan bungkus dengan AgentExecutor
    agent = create_react_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True, memory=memory)

    return executor
