import json
import subprocess
from pathlib import Path

from config.schema import AgentConfig

PRISMA_DIR = Path(__file__).resolve().parent / "prisma"
SCRIPT = PRISMA_DIR / "agent_service.js"


def create_agent_record(owner_id: str, name: str, config: AgentConfig) -> str:
    payload = {
        "ownerId": owner_id,
        "name": name,
        "config": config.dict(),
    }
    result = subprocess.run(
        ["node", str(SCRIPT), "create"],
        cwd=str(PRISMA_DIR),
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data["id"]


def get_agent_config(agent_id: str) -> AgentConfig:
    payload = {"agent_id": agent_id}
    result = subprocess.run(
        ["node", str(SCRIPT), "get"],
        cwd=str(PRISMA_DIR),
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return AgentConfig(
        model_name=data["modelName"],
        system_message=data["systemMessage"],
        tools=data["tools"],
        memory_enabled=data["memoryEnabled"],
    )
