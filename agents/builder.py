# Build agent from config
# agents/builder.py

from langchain.chat_models import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
from config.schema import AgentConfig
from agents.tools.registry import get_tools_by_names
from agents.memory import get_memory_if_enabled

def build_agent(config: AgentConfig):
    """
    Membangun LangChain agent berdasarkan AgentConfig:
    - model_name (mis. "gpt-4")
    - system_message (prompt awal system)
    - tools (list nama tool)
    - memory_enabled (True/False)
    """
    # 1. Inisiasi LLM
    llm = ChatOpenAI(model_name=config.model_name, temperature=0)

    # 2. Ambil tool dari registry sesuai nama
    tools = get_tools_by_names(config.tools)

    # 3. Siapkan memory jika diaktifkan
    memory = get_memory_if_enabled(config.memory_enabled)

    # 4. Inisialisasi agent
    agent = initialize_agent(
        tools=tools,
        llm=llm,
        agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
        verbose=True,
        memory=memory
    )

    return agent
