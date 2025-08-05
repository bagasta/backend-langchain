# LangChain Modular Backend

Backend framework for building configurable LangChain agents through a REST API.

## Prerequisites
- Python 3.10+
- Node.js 18+
- PostgreSQL (for Prisma ORM)
- Environment variables such as `OPENAI_API_KEY` for LLM access (or pass `openai_api_key` in the request payload). Values from a `.env` file are loaded automatically.

## Setup
```bash
pip install -r requirements.txt
npm install
cd database/prisma && npx prisma migrate deploy && cd ../..
```

## Running the API
```bash
uvicorn main:app --reload
```

## Example Usage
Create an agent:
```bash
curl -X POST http://localhost:8000/agents/ \
  -H 'Content-Type: application/json' \
  -d '{
        "owner_id": "user1",  # a placeholder user is auto-created if this ID doesn't exist
        "name": "demo",
        "config": {
          "model_name": "gpt-4",
          "system_message": "You are a helpful bot",
          "tools": ["calc", "google"],
          "memory_enabled": true,
          "agent_type": "openai-functions",
          "max_iterations": 25
        }
      }'
```

Run the agent by ID:
```bash
curl -X POST http://localhost:8000/agents/{agent_id}/run \
  -H 'Content-Type: application/json' \
  -d '{
        "message": "What is 2 + 2?",
        "openai_api_key": "sk-..."
      }'
```

Both endpoints accept optional limits: set `max_iterations` or `max_execution_time` in the agent configuration to control how long an agent may run before aborting.

The `agent_type` field accepts any value from LangChain's [`AgentType` enumeration](https://api.python.langchain.com/en/latest/agents/langchain.agents.agent_types.AgentType.html), enabling different execution strategies beyond the default ReAct-style agent.

## Extending
- **Tools**: add a module under `agents/tools/` and register it in `agents/tools/registry.py`.
- **Memory**: modify or extend `agents/memory.py`.

## Testing
```bash
pytest
```
