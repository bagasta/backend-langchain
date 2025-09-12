# API Reference

Base URL: `http://localhost:8000`

This backend exposes endpoints to create and run configurable agents, perform Gmail helper actions, and handle Google OAuth callbacks.

## Agents

### Create Agent
- Method: `POST`
- Path: `/agents/`
- Body (JSON):
  - `owner_id` (string) — required
  - `name` (string) — required
  - `config` (object, AgentConfig) — required
    - `model_name` (string) — required
    - `system_message` (string) — required
    - `tools` (string[]) — required; Gmail/calendar/maps/docs names are auto‑expanded to concrete tools when needed
    - `memory_enabled` (boolean) — optional, default `false`
    - `memory_backend` (string: `in_memory` | `sql` | `file`) — optional, default `in_memory`
    - `agent_type` (string) — optional; defaults to `chat-conversational-react-description`
    - `max_iterations` (int) — optional
    - `max_execution_time` (float seconds) — optional
    - `openai_api_key` (string) — ignored on create (not persisted)
- Response (200):
  - `agent_id` (string)
  - `auth_urls` (object) — optional; when any Google tools are present, includes a single unified `google` OAuth URL with the full scope set (Gmail + Calendar + Docs)
- Notes:
  - Tool naming: use canonical `google_*` names for Google providers, e.g. `google_gmail`, `google_calendar`, `google_docs`, `google_maps`. Legacy aliases like `gmail`, `calendar`, `docs`, `maps` are accepted and normalized.
  - Gmail tool names (e.g., `google_gmail` or `gmail`) are auto‑expanded so a single umbrella entry enables read/get/send actions.
  - The server upserts the `User` row for `owner_id` if it does not exist.
  - The persisted agent config excludes `openai_api_key`.

Example:
```
POST /agents/
{
"owner_id": "3",
"name": "demo9",
"config": {
"model_name": "gpt-4o-mini",
"system_message": "You are helpful",
"tools": ["calc"],
"memory_enabled": false
}
}
```

Curl:
```
curl --location 'http://localhost:8000/agents/' \
--header 'Content-Type: application/json' \
--data '{
"owner_id": "3",
"name": "demo9",
"config": {
"model_name": "gpt-4o-mini",
"system_message": "You are helpful",
"tools": ["calc"],
"memory_enabled": false
}
}'
```

### Run Agent
- Method: `POST`
- Path: `/agents/{agent_id}/run`
- Body (JSON):
  - `message` (string) — required
  - `openai_api_key` (string) — optional; preferred way to supply per‑request key
  - `config` (AgentConfig) — optional; if provided, bypasses DB/cache and runs with this config directly
  - `sessionId` (string) — optional; stable chat id for memory partitioning. Reuse this value to continue the same chat.
  - `memory_enable` (boolean) — optional; per‑run override for memory (default uses agent config)
  - `context_memory` (int|string) — optional; per‑run limit of past messages to load (latest N)
  - `rag_enable` (boolean) — optional; per‑run RAG toggle (default uses `RAG_ENABLED` env)
  - `owner_id` (string) — optional; pass if you already know the owner/user id to avoid a lookup
- Response (200): `{ "response": string }` or an error string if execution failed (tool/LLM error)
- Errors:
  - 400 for configuration issues (e.g., missing API key)
  - 400 with guidance if cache bypass is enabled and the agent config is not warmed
  - 500 for unexpected runtime errors
- Behavior:
  - When `RUN_BYPASS_DB=true` (default), the server reads config from in‑process/file cache. Warm it first or pass `config` inline.

Example:
```
POST /agents/{agent_id}/run
{
  "message": "What is 2 + 2?",
  "openai_api_key": "sk-...",
  "sessionId": "1",
  "memory_enable": true,
  "context_memory": 100
}
```

Curl:
```
curl --location 'http://localhost:8000/agents/{agentId}/run' \
--header 'Content-Type: application/json' \
--data '{
        "message": "Siapa nama saya?",
        "openai_api_key":"API KEY",
        "sessionId":"1",
        "memory_enable": false,
        "context_memory": "10"
      }'
```

### Warm Agent Cache
- Method: `POST`
- Path: `/agents/{agent_id}/warm`
- Response (200): `{ "agent_id": string, "ok": boolean, "detail"?: string }`
- Purpose: Populate/refresh file + memory cache for `agent_id` so runs can avoid DB calls.

Curl:
```
curl -X POST http://localhost:8000/agents/AGENT_ID/warm
```

### Warm All Agents
- Method: `POST`
- Path: `/agents/warm_all`
- Response (200): `{ "warmed": number, "skipped": number, "errors": number, "total": number }`

Curl:
```
curl -X POST http://localhost:8000/agents/warm_all
```

## Gmail Helpers

These endpoints help test connectivity and perform simple actions outside the agent loop.

### Status
- Method: `GET`
- Path: `/gmail/status`
- Response: diagnostic fields: credential paths, scopes, and connection checks (see `router/gmail_status.py`).

Curl:
```
curl -X GET http://localhost:8000/gmail/status
```

### Dry Send (validate scopes)
- Method: `POST`
- Path: `/gmail/dry_send`
- Body: `{ "to": string, "subject": string, "text": string }`
- Response: `{ "ok": boolean, "reason"?: string, "missing_scopes": string[] }`
- Method: `GET` at the same path returns guidance text for browser testing.

Curl (POST):
```
curl -X POST http://localhost:8000/gmail/dry_send \
  -H 'Content-Type: application/json' \
  -d '{
        "to": "recipient@example.com",
        "subject": "Test",
        "text": "Hello from curl"
      }'
```

Curl (GET helper):
```
curl -X GET http://localhost:8000/gmail/dry_send
```

### Send Email
- Method: `POST`
- Path: `/gmail/send`
- Body: `{ "to": string, "subject": string, "message": string }`
- Response: `{ "ok": boolean, "id"?: string, "error"?: string }`

Curl:
```
curl -X POST http://localhost:8000/gmail/send \
  -H 'Content-Type: application/json' \
  -d '{
        "to": "recipient@example.com",
        "subject": "Hello",
        "message": "This is a test from curl"
      }'
```

### Read Messages
- Method: `POST`
- Path: `/gmail/read`
- Body: `{ "query"?: string, "max_results"?: number (1..50), "mark_as_read"?: boolean }`
- Response: `{ "ok": boolean, "messages"?: object[], "error"?: string }`

Curl:
```
curl -X POST http://localhost:8000/gmail/read \
  -H 'Content-Type: application/json' \
  -d '{
        "query": "in:inbox is:unread",
        "max_results": 5,
        "mark_as_read": false
      }'
```

## OAuth Callbacks

The server supports provider‑specific and universal Google OAuth callbacks. Configure the corresponding redirect URIs in your Google Cloud OAuth client.

### Gmail Callback
- Method: `GET`
- Path: `/oauth/gmail/callback`
- Query: `code`, `state?` (optional agent id)
- Writes `token.json` next to `credentials.json` (or at `GMAIL_TOKEN_PATH`).

Curl (for testing, normally called by Google redirect):
```
curl "http://localhost:8000/oauth/gmail/callback?code=AUTH_CODE&state=AGENT_ID"
```

### Calendar Callback
- Method: `GET`
- Path: `/oauth/calendar/callback`
- Query: `code`, `state?`
- Writes `calendar_token.json` (or at `GCAL_TOKEN_PATH`).

Curl (for testing, normally called by Google redirect):
```
curl "http://localhost:8000/oauth/calendar/callback?code=AUTH_CODE&state=AGENT_ID"
```

### Universal Google Callback
- Method: `GET`
- Path: `/oauth/google/callback`
- Query: `code`, `state?`, `scope`
- Infers provider(s) from scopes, writes tokens for Gmail/Calendar/Docs to their respective paths.
- Additionally stores a unified token per agent in the database table `list_account` when `state` carries the agent ID.

Curl (for testing, normally called by Google redirect):
```
curl "http://localhost:8000/oauth/google/callback?code=AUTH_CODE&state=AGENT_ID&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.modify%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdocuments%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file"
```

## AgentConfig (schema)
- `model_name`: string
- `system_message`: string
- `tools`: string[]
- `memory_enabled`: boolean (default true)
- `memory_backend`: `in_memory` | `sql` | `file` (default `sql`)
- `memory_max_messages`: int (optional; default null → load all)
- `agent_type`: string (default `chat-conversational-react-description`)
- `max_iterations`: int (optional)
- `max_execution_time`: float seconds (optional)
- `openai_api_key`: string (never persisted; provide at run‑time)

## Notes
- OpenAI key: provide via `openai_api_key` on `/agents/{id}/run` or environment; it is not saved in the database.
- Tool expansion: shorthand names (e.g., `gmail`, `google_calendar`, `maps`, `docs`) are expanded to concrete tools for capability coverage.
- Caching: agent configs are cached in memory and under `database/cache/agents/{id}.json` for faster runs and DB resilience.

## RAG Behavior
- Default embedding model `text-embedding-3-large` (3072 dims). Fallback auto‑retry handles mixed 1536/3072 setups.
- Knowledge tables live in `KNOWLEDGE_DATABASE_URL` (or derived) with `vector(3072)` embeddings.
- Logs can be toggled via `RAG_LOG_CONTEXT`, `RAG_LOG_SYSTEM_MESSAGE`, `RAG_SNIPPET_PREVIEW_CHARS`.

## Memory Behavior
- SQL backend stores messages in `public."memory_{userId}{agentId}"` with columns `(id serial, session_id varchar(255), message text)`.
- Session routing: pass `sessionId` in `/run`; the server routes to `"{userId}:{agentId}|{sessionId}"` and stores `session_id = sessionId`.
- To avoid duplicates entirely, set `MEMORY_FALLBACK_WRITE=false` (disables fallback writer). When enabled (default), fallback inserts only if rows do not already exist for the same content.
