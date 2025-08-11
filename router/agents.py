# Endpoint for creating and running agents
# router/agents.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config.schema import AgentConfig
from agents.runner import run_custom_agent
from agents.tools.registry import get_auth_urls
from database.client import create_agent_record, get_agent_config

router = APIRouter()


class RunAgentRequest(BaseModel):
    """Payload model for running an agent."""
    message: str
    openai_api_key: str | None = None


class CreateAgentRequest(BaseModel):
    """Payload model for creating an agent."""
    owner_id: str
    name: str
    config: AgentConfig


@router.post("/", summary="Create an agent")
async def create_agent(payload: CreateAgentRequest):
    try:
        agent_id = create_agent_record(payload.owner_id, payload.name, payload.config)
    except Exception as exc:  # pragma: no cover - DB errors
        raise HTTPException(status_code=500, detail=str(exc))
    auth_urls = get_auth_urls(payload.config.tools, state=agent_id)
    response = {"agent_id": agent_id}
    if auth_urls:
        response["auth_urls"] = auth_urls
    return response

@router.post("/{agent_id}/run", summary="Run an agent by ID")
async def run_agent(agent_id: str, payload: RunAgentRequest):
    try:
        config = get_agent_config(agent_id)
        if payload.openai_api_key:
            config.openai_api_key = payload.openai_api_key
        result = run_custom_agent(agent_id, config, payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - runtime errors
        raise HTTPException(status_code=500, detail=str(exc))
    return {"response": result}
