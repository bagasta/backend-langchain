# Endpoint for creating and running agents
# router/agents.py
from fastapi import APIRouter, HTTPException
import os
from pydantic import BaseModel
from config.schema import AgentConfig
from agents.runner import run_custom_agent
from agents.tools.registry import get_auth_urls, expand_tool_names
from database.client import (
    create_agent_record,
    get_agent_config,
    warm_cache_for_agent,
    warm_cache_for_all,
    get_cached_agent_config,
)

router = APIRouter()


class RunAgentRequest(BaseModel):
    """Payload model for running an agent."""
    message: str
    openai_api_key: str | None = None
    # Optional client-managed chat session id used for memory partitioning
    sessionId: str | None = None
    # Optional per-run memory toggle and context limit
    memory_enable: bool | None = None
    context_memory: int | str | None = None
    # Optional: bypass DB by providing config directly
    config: AgentConfig | None = None


class CreateAgentRequest(BaseModel):
    """Payload model for creating an agent."""
    owner_id: str
    name: str
    config: AgentConfig


@router.post("/", summary="Create an agent")
async def create_agent(payload: CreateAgentRequest):
    try:
        # Expand tool list to ensure full Gmail capabilities if any Gmail tool is requested
        try:
            expanded = expand_tool_names(payload.config.tools)
            payload.config.tools = expanded
        except Exception:
            pass
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
        # 1) If payload carries the config, use it and bypass storage
        if payload.config is not None:
            # Expand config.tools for convenience when clients pass a shorthand
            try:
                payload.config.tools = expand_tool_names(payload.config.tools)
            except Exception:
                pass
            config = payload.config
        else:
            # 2) Optionally bypass DB entirely and use cache-only
            bypass_db = os.getenv("RUN_BYPASS_DB", "true").lower() == "true"
            if bypass_db:
                cfg = get_cached_agent_config(agent_id)
                if cfg is None:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Agent config not cached. Warm it first via POST /agents/{agent_id}/warm "
                            "or pass `config` in the run payload."
                        ),
                    )
                # Expand tools for older agents created before Gmail auto-expansion
                try:
                    cfg.tools = expand_tool_names(cfg.tools)
                except Exception:
                    pass
                config = cfg
            else:
                # 3) Default to DB + caches when bypass is disabled
                cfg = get_agent_config(agent_id)
                try:
                    cfg.tools = expand_tool_names(cfg.tools)
                except Exception:
                    pass
                config = cfg
        if payload.openai_api_key:
            config.openai_api_key = payload.openai_api_key
        # Apply per-run memory overrides
        try:
            if payload.memory_enable is not None:
                config.memory_enabled = bool(payload.memory_enable)
        except Exception:
            pass
        try:
            if payload.context_memory is not None:
                cm = int(str(payload.context_memory).strip())
                if cm < 0:
                    cm = 0
                config.memory_max_messages = cm
        except Exception:
            pass
        result = run_custom_agent(agent_id, config, payload.message, session_id=payload.sessionId)
    except ValueError as exc:
        msg = str(exc)
        # If the agent failed during execution (e.g., tool/LLM error), return 200 with the error text
        # so frontends that expect a normal response can display it, while preserving 400s for config errors.
        if msg.startswith("Agent execution failed:"):
            return {"response": msg}
        raise HTTPException(status_code=400, detail=msg)
    except Exception as exc:  # pragma: no cover - runtime errors
        raise HTTPException(status_code=500, detail=str(exc))
    return {"response": result}


class WarmResponse(BaseModel):
    agent_id: str
    ok: bool
    detail: str | None = None


@router.post("/{agent_id}/warm", summary="Warm cache for an agent")
async def warm_agent(agent_id: str):
    try:
        cfg = warm_cache_for_agent(agent_id)
        return WarmResponse(agent_id=agent_id, ok=True)
    except Exception as exc:  # pragma: no cover - runtime errors
        return WarmResponse(agent_id=agent_id, ok=False, detail=str(exc))


class WarmAllResponse(BaseModel):
    warmed: int
    skipped: int
    errors: int
    total: int


@router.post("/warm_all", summary="Warm cache for all agents")
async def warm_all():
    try:
        stats = warm_cache_for_all()
        return WarmAllResponse(**stats)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))
