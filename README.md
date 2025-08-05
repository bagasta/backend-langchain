# LangChain Modular Backend

Backend framework for building configurable LangChain agents through a REST API.

## Prerequisites
- Python 3.10+
- Node.js 18+
- PostgreSQL (for Prisma ORM)
- Environment variables such as `OPENAI_API_KEY` for LLM access

## Setup
```bash
pip install -r requirements.txt
npm install
```

## Running the API
```bash
uvicorn main:app --reload
```

## Example Usage
```bash
curl -X POST http://localhost:8000/agents/{agent_id}/run \
  -H 'Content-Type: application/json' \
  -d '{
        "config": {
          "model_name": "gpt-4",
          "system_message": "You are a helpful bot",
          "tools": ["calc"],
          "memory_enabled": true
        },
        "message": "What is 2 + 2?"
      }'
```

## Extending
- **Tools**: add a module under `agents/tools/` and register it in `agents/tools/registry.py`.
- **Memory**: modify or extend `agents/memory.py`.

## Testing
```bash
pytest
```
