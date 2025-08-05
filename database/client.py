import json
import subprocess
from pathlib import Path

from config.schema import AgentConfig

PRISMA_DIR = Path(__file__).resolve().parent / "prisma"
SCRIPT = PRISMA_DIR / "agent_service.js"


def _run(command: str, payload: dict) -> dict:
    try:
        result = subprocess.run(
            ["node", str(SCRIPT), command],
            cwd=str(PRISMA_DIR),
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.strip() or f"Node command failed: {exc.cmd}"
        raise RuntimeError(msg) from exc
    return json.loads(result.stdout)


def create_agent_record(owner_id: str, name: str, config: AgentConfig) -> str:
    data = _run(
        "create",
        {
            "ownerId": owner_id,
            "name": name,
            "config": config.model_dump(),
        },
    )
    return data["id"]


def get_agent_config(agent_id: str) -> AgentConfig:
    data = _run("get", {"agent_id": agent_id})
    return AgentConfig(
        model_name=data["modelName"],
        system_message=data["systemMessage"],
        tools=data["tools"],
        memory_enabled=data["memoryEnabled"],
    )
