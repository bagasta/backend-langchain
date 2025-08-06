# LangChain Modular Backend

Backend framework for building configurable LangChain agents through a REST API.

## Prerequisites
- Python 3.10+
- Node.js 18+
- PostgreSQL (for Prisma ORM)
- PostgreSQL driver (`psycopg2-binary` is included in `requirements.txt`)
- Environment variables such as `OPENAI_API_KEY` for LLM access (or pass `openai_api_key` in the request payload). Values from a `.env` file are loaded automatically.

## Setup
```bash
pip install -r requirements.txt
npm install
cd database/prisma && npx prisma migrate deploy && cd ../..
```

The Python database wrapper automatically runs `prisma migrate deploy` and `prisma generate` before each operation to keep the
Prisma client in sync with the schema. Still, ensure your PostgreSQL instance is reachable via `DATABASE_URL`.

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
          "memory_backend": "sql",
          "max_iterations": 25,
          "agent_type": "chat-conversational-react-description"  # optional; convers.
        }
      }'
```
The backend builds a LangChain **ConversationalAgent**, so only `conversational-react-description` and `chat-conversational-react-description` are supported. Supplying other `agent_type` values such as `openai-functions` will raise an error.

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

### Conversation Memory

When `memory_enabled` is `true`, each agent stores its conversation history using the selected backend:

- `sql` (default when `DATABASE_URL` is provided) persists history in the configured database.
- `file` writes each conversation to `MEMORY_DIR` as JSON.
- `in_memory` keeps history only for the current process.

History is keyed by the agent ID, so subsequent runs recall prior messages when using a persistent backend.

## Extending
- **Tools**: add a module under `agents/tools/` and register it in `agents/tools/registry.py`.
- **Memory**: modify or extend `agents/memory.py`.

## Testing
```bash
pytest
```
