# Endpoint for creating and running agents
# router/agents.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config.schema import AgentConfig
from agents.runner import run_custom_agent
# note: nanti load config dari DB via Prisma microservice

router = APIRouter()


class RunAgentRequest(BaseModel):
    """Payload model for running an agent."""
    config: AgentConfig
    message: str

@router.post("/", summary="(Stub) Create an agent")
async def create_agent(config: AgentConfig):
    # TODO: panggil microservice Prisma untuk simpan config
    return {"agent_id": "stub-id"}

@router.post("/{agent_id}/run", summary="Run an agent by ID")
async def run_agent(agent_id: str, payload: RunAgentRequest):
    # TODO: fetch config dari DB (Prisma) berdasarkan agent_id
    # untuk sekarang kita stub config langsung dari payload
    try:
        result = run_custom_agent(payload.config, payload.message)
    except Exception as exc:  # pragma: no cover - runtime errors
        raise HTTPException(status_code=500, detail=str(exc))
    return {"response": result}
