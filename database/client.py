import json
import subprocess
from pathlib import Path

from config.schema import AgentConfig

PRISMA_DIR = Path(__file__).resolve().parent / "prisma"
SCRIPT = PRISMA_DIR / "agent_service.js"


def _run(command: str, payload: dict) -> dict:
    try:
        subprocess.run(
            ["npx", "prisma", "migrate", "deploy"],
            cwd=str(PRISMA_DIR),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["npx", "prisma", "generate"],
            cwd=str(PRISMA_DIR),
            capture_output=True,
            check=True,
        )
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
            "config": config.model_dump(exclude={"openai_api_key"}),
        },
    )
    return data["id"]


def get_agent_config(agent_id: str) -> AgentConfig:
    data = _run("get", {"agent_id": agent_id})
    payload = {
        "model_name": data["modelName"],
        "system_message": data["systemMessage"],
        "tools": data["tools"],
        "memory_enabled": data["memoryEnabled"],
        "memory_backend": data.get("memoryBackend", "in_memory"),
    }
    if data.get("agentType") is not None:
        payload["agent_type"] = data["agentType"]
    if data.get("maxIterations") is not None:
        payload["max_iterations"] = data["maxIterations"]
    if data.get("maxExecutionTime") is not None:
        payload["max_execution_time"] = data["maxExecutionTime"]
    return AgentConfig(**payload)
