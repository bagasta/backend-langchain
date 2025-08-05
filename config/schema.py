# Pydantic model for agent config
# config/schema.py

from pydantic import BaseModel
from typing import List, Optional

class AgentConfig(BaseModel):
    model_name: str
    system_message: str
    tools: List[str]
    memory_enabled: Optional[bool] = False
    openai_api_key: Optional[str] = None
