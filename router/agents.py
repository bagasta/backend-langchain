# Endpoint for creating and running agents
# router/agents.py
from fastapi import APIRouter, HTTPException
from config.schema import AgentConfig
from agents.runner import run_custom_agent
# note: nanti load config dari DB via Prisma microservice

router = APIRouter()

@router.post("/", summary="(Stub) Create an agent")
async def create_agent(config: AgentConfig):
    # TODO: panggil microservice Prisma untuk simpan config
    return {"agent_id": "stub-id"}

@router.post("/{agent_id}/run", summary="Run an agent by ID")
async def run_agent(agent_id: str, payload: dict):
    # TODO: fetch config dari DB (Prisma) berdasarkan agent_id
    # untuk sekarang kita stub config langsung dari payload
    cfg = AgentConfig(**payload.get("config"))
    result = run_custom_agent(cfg, payload["message"])
    return {"response": result}
