# Pydantic model for agent config
# config/schema.py

from pydantic import BaseModel
from typing import List, Optional
from langchain.agents import AgentType

class AgentConfig(BaseModel):
    model_name: str
    system_message: str
    tools: List[str]
    memory_enabled: Optional[bool] = False
    openai_api_key: Optional[str] = None
    max_iterations: Optional[int] = None
    max_execution_time: Optional[float] = None
    agent_type: Optional[str] = AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION.value
