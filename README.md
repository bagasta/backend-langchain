# LangChain Modular Backend

Backend framework for building configurable LangChain agents through a REST API.

## Prerequisites
- Python 3.10+
- Node.js 18+
- PostgreSQL (for Prisma ORM)
- PostgreSQL driver (`psycopg2-binary` is included in `requirements.txt`)
- Environment variables such as `OPENAI_API_KEY` for LLM access (or pass `openai_api_key` in the request payload). Values from a `.env` file are loaded automatically.
- For the `spreadsheet` tool, `GOOGLE_APPLICATION_CREDENTIALS` must point to a Google service-account JSON. Optionally set
  `SPREADSHEET_ID` to avoid passing the sheet ID in every request; worksheet names are matched case-insensitively and default to
  the first sheet when omitted. Set `SPREADSHEET_TIMEOUT` (seconds) to limit request time and surface logs if Google API calls hang.
- For Gmail tools, you can now drop your OAuth files in a project folder instead of using absolute paths:
  - Preferred: place `credentials.json` (client secrets) and optionally `token.json` under `./.credentials/gmail/`.
  - Or set `GMAIL_CREDENTIALS_DIR` (or `CREDENTIALS_DIR` + `/gmail`) to point elsewhere; defaults to `./.credentials/gmail`.
  - You can still override paths via `GMAIL_CLIENT_SECRETS_PATH` and `GMAIL_TOKEN_PATH`.
  - If neither is set, the backend also falls back to `GOOGLE_APPLICATION_CREDENTIALS` for locating `credentials.json`.
  - Set `GMAIL_REDIRECT_URI` for OAuth callbacks and override `GMAIL_SCOPES` to customize API permissions.
  When an agent is created with Gmail tools, the API returns an `auth_urls.gmail` link users can visit to grant access. After the first OAuth flow, `token.json` will be created/used automatically.

### Gmail OAuth callback

- The backend exposes an OAuth callback endpoint at `/oauth/gmail/callback`.
- Configure `GMAIL_REDIRECT_URI` to point to it (e.g., `http://localhost:8000/oauth/gmail/callback`).
- After visiting the `auth_urls.gmail` link, Google redirects back to this endpoint and the server saves the token to `token.json` under your credentials directory.

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
          "tools": ["calc", "google_search", "websearch", "spreadsheet"],
          "memory_enabled": true,
          "memory_backend": "sql",
          "max_iterations": 25,
          "agent_type": "chat-conversational-react-description"  # optional
        }
      }'
```
The backend builds a LangChain **tool-calling agent** capable of invoking multiple tools in sequence. Only `conversational-react-description` and `chat-conversational-react-description` types are accepted; other `agent_type` values (e.g., `openai-functions`) will raise an error. If Gmail tools are included, the response also contains an `auth_urls.gmail` login link for granting access.

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
  Built-in tools include math evaluation (`calc`), numerous Google integrations (`google_search`, `google_serper`, `google_trends`, `google_places`, `google_finance`, `google_cloud_text_to_speech`, `google_jobs`, `google_scholar`, `google_books`, `google_lens`, `gmail_search`, `gmail_send_message`), an OpenAI-powered product lookup (`websearch`), and Google Sheets operations (`spreadsheet`).
- **Memory**: modify or extend `agents/memory.py`.

## Testing
```bash
pytest
```
