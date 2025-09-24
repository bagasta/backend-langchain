# LangChain Modular Backend

Backend framework for building configurable LangChain agents through a REST API.

Quick links:
- API Reference: `docs/API_REFERENCE.md`
- Database Structure: `docs/DB_SCHEMA.md`

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
  - For convenience, the server will also look for `credentials.json` and `token.json` at the project root if present.
  - Set `GMAIL_REDIRECT_URI` for OAuth callbacks and override `GMAIL_SCOPES` to customize API permissions.
  When an agent includes any Google tools (Gmail/Calendar/Docs), the API returns a single `auth_urls.google` link for a unified OAuth flow that grants all scopes at once. The universal callback also stores the token per‑agent in the database table `list_account`.

  Gmail tools include:
  - `gmail`: unified Gmail node (like n8n) with `action` = `read | search | send | get`. Fields:
    - read: `query?`, `max_results?`, `mark_as_read?`
    - search: `query`, `max_results?`
    - send: `to`, `subject`, `message`, `is_html?`
    - get: `message_id`, `format?` (one of `minimal|full|raw|metadata`)
  - `gmail_get_message`: fetch a single Gmail message by `message_id` (optionally `format`)
  - `gmail_search`: search messages (subjects, from, snippet) using a Gmail query
  - `gmail_read_messages`: read recent messages (defaults to `in:inbox is:unread`) and return bodies; supports `max_results` and `mark_as_read`
  - `gmail_send_message`: send an email (plain text or HTML)

  Notes:
  - If you include any Gmail tool (including the unified `"gmail"`) when creating an agent, the server automatically enables all core actions by expanding the tool list to include `gmail_read_messages`, `gmail_get_message`, and `gmail_send_message` as well.
  - The create‑agent response will include a single `auth_urls.google` link covering Gmail, Calendar, and Docs scopes.

  Direct testing endpoints (outside agent loop):
  - `GET /gmail/status` — shows credential paths, scopes, and connection status
  - `POST /gmail/send` — send an email with JSON body `{to, subject, message}`
  - `POST /gmail/read` — read messages with JSON body `{query?, max_results?, mark_as_read?}`

### Google OAuth (Gmail + Calendar)

- Use a single redirect URI for both Gmail and Calendar:
  - Set `GOOGLE_OAUTH_REDIRECT_URI` (or `OAUTH_REDIRECT_URI`) to point to the universal callback: `/oauth/google/callback`.
  - Add that URI to your Google OAuth "Web application" client's Authorized redirect URIs.
  - The server infers provider(s) from the scopes and writes tokens to the correct files.
- You can still use provider-specific callbacks if you prefer:
  - Gmail: set `GMAIL_REDIRECT_URI` → `/oauth/gmail/callback`
  - Calendar: set `GCAL_REDIRECT_URI` → `/oauth/calendar/callback`
- Place the client secrets at `./credential_folder/credentials.json` (or set `GMAIL_CLIENT_SECRETS_PATH`/`GCAL_CLIENT_SECRETS_PATH`).
- Tokens
  - Gmail token: `GMAIL_TOKEN_PATH` (default `credential_folder/token.json`)
  - Calendar token: `GCAL_TOKEN_PATH` (default `credential_folder/calendar_token.json`)
  - Docs token: `GDOCS_TOKEN_PATH` (default `credential_folder/docs_token.json`)
  - Tokens are separate by default to avoid scope conflicts.
  - Attendees convenience: when creating Calendar events, if an attendee address is given without a domain (e.g., `"bagasstorage"`), the server appends `@gmail.com` by default. Override the default domain with `GCAL_DEFAULT_EMAIL_DOMAIN` (e.g., `yourcompany.com`). Invalid addresses are ignored and surfaced in the response.

### Google Maps (API key)

- Set `GOOGLE_MAPS_API_KEY` (or `MAPS_API_KEY`) in your environment.
- Available tools:
  - `google_maps` (unified): actions = `geocode | reverse_geocode | directions | distance_matrix | timezone | nearby`.
    Example (directions):
    `{ "action": "directions", "origin": "Jakarta", "destination": "Bandung", "mode": "driving" }`
  - Convenience aliases:
    - `maps_geocode`: input = address string
    - `maps_directions`: input = `origin|destination|mode?` (e.g., `Jakarta|Bandung|driving`)
    - `maps_distance_matrix`: input = `origin|destination|mode?`
    - `maps_nearby`: input = `address|type|radius?` or `lat,lng|type|radius?` (e.g., `Clevio Coder Camp|pharmacy` or `-6.2,106.8|pharmacy|1500`). If you omit radius, the tool ranks by distance and requires a `type` or `keyword`.

### Google Docs (OAuth)

- Scopes: `https://www.googleapis.com/auth/documents, https://www.googleapis.com/auth/drive.file` (override with `GDOCS_SCOPES`).
- Redirect: use the universal `GOOGLE_OAUTH_REDIRECT_URI` or set `GDOCS_REDIRECT_URI` and add it to your OAuth client.
- Token path: `GDOCS_TOKEN_PATH` (default `credential_folder/docs_token.json`).
- Tools:
  - `google_docs` (unified): actions = `create|get|append|export`
  - `docs_create`, `docs_get`, `docs_append`, `docs_export_pdf` (convenience actions)
  - `docs_export_pdf` saves to `./exports/{document_id}.pdf` on the server.

### Gmail OAuth callback

- The backend exposes an OAuth callback endpoint at `/oauth/gmail/callback`.
- Configure `GMAIL_REDIRECT_URI` to point to it (e.g., `http://localhost:8000/oauth/gmail/callback`).
- After visiting the `auth_urls.gmail` link, Google redirects back to this endpoint and the server saves the token to `token.json` under your credentials directory.

Scopes
- Default scopes: `gmail.modify` and `gmail.send` (sufficient for reading, marking read, and sending).
- For the broadest compatibility with Gmail operations (similar to n8n's Gmail node), you may use the full scope `https://mail.google.com/` by setting `GMAIL_SCOPES=https://mail.google.com/` before authorizing.

## Setup
```bash
pip install -r requirements.txt
npm install
cd database/prisma && npx prisma migrate deploy && npx prisma generate && cd ../..
```

By default, the backend no longer runs Prisma CLI on every request. This avoids slow responses and timeouts caused by repeatedly
running `npx prisma migrate deploy`/`generate` in-process. Do an explicit one-time setup as shown above. If you prefer the old
behavior, set `PRISMA_AUTO_SYNC=true` (it will run once per process) and optionally tune `PRISMA_CMD_TIMEOUT` (seconds).

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
        "agent_name": "demo-agent-1",
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

### Retrieval‑Augmented Generation (RAG)

- Default embedding model: `text-embedding-3-large` (3072 dims). Override with `EMBEDDING_MODEL`.
- Knowledge DB: `KNOWLEDGE_DATABASE_URL` or derived from `DATABASE_URL` as `/knowledge_clevio_pro`.
- Per‑agent knowledge tables: `public."tb_{userId}_{agentId}"` with `embedding vector(N)` (default `3072`). Override `KNOWLEDGE_VECTOR_DIM`/`RAG_VECTOR_DIM` when using different embedding models. Set `RAG_IVFFLAT_MAX_DIM` (default `2000`) when you need to disable IVFFlat index creation for higher-dimension embeddings.
- Logs:
  - `RAG_LOG_CONTEXT=true|false` shows snippet previews.
  - `RAG_LOG_SYSTEM_MESSAGE=true|false` prints full system message with injected context.
  - `RAG_SNIPPET_PREVIEW_CHARS=200` controls preview length.
- Tuning:
  - `RAG_TOP_K` (default `3`) keeps retrieval tight for faster responses.
  - `RAG_MIN_SIMILARITY` (default `0.2`) skips context injection when nothing matches, avoiding extra tokens and latency.
  - `FAST_RAG_RESPONSE=true|false` enables a lightweight response path (no agent/tool run) when only RAG snippets are needed. Use `FAST_RAG_MODEL`, `FAST_RAG_TIMEOUT`, and `FAST_RAG_MAX_TOKENS` to tune it.
  - `FAST_RAG_STREAM=true|false` streams RAG-only answers token by token (default `true`).

### Conversation Memory

When `memory_enabled` is `true`, the agent tracks conversation state. Backends:

- `sql` (default): uses `MEMORY_DATABASE_URL` (fallback to `DATABASE_URL`).
  - Per‑agent tables: `public."memory_{userId}_{agentId}"` with schema `(id serial, session_id varchar(255), message text)`.
  - Session routing: pass `sessionId` when running; a run uses token `"{userId}:{agentId}|{sessionId}"` internally so history loads from the correct table but stores `session_id` as the chat id (after the `|`).
  - Fallback writer: set `MEMORY_FALLBACK_WRITE=false` to rely solely on LangChain’s writer (prevents duplicates). When `true` (default), the fallback inserts only if rows for the same content aren’t already present.
- `file`: JSON files under `MEMORY_DIR`.
- `in_memory`: process‑local only.

Limit context size per run by setting `context_memory` in the run payload to load only the last N messages (keeps responses fast).

## Timeouts and Reliability
- Database (Prisma) calls respect `PRISMA_CMD_TIMEOUT` (default 15s). Ensure `DATABASE_URL` points to a reachable Postgres server.
  For automatic sync at startup, set `PRISMA_AUTO_SYNC=true`.
- OpenAI chat requests use `OPENAI_TIMEOUT` (default 30s) and `OPENAI_MAX_RETRIES` (default 1).
- Gmail REST operations use `GMAIL_HTTP_TIMEOUT` (default 20s) to prevent long hangs.
- Agent config caching: fetched configs are cached in-process for `AGENT_CACHE_TTL` seconds (default 300). If Prisma fails
  transiently on later requests, the cache avoids the DB round-trip so agents can keep responding.

## Performance Tips (sub-7s runs)
- Bypass DB on runs: keep `RUN_BYPASS_DB=true` and warm the cache via `/agents/{id}/warm`.
- Owner id caching: the server now caches `owner_id` per agent to avoid Node/Prisma calls on every run.
- RAG fast-fail and toggle:
  - Set `RAG_ENABLED=false` to skip retrieval entirely when you don’t need it.
  - Tune `RAG_STATEMENT_TIMEOUT_MS` (default `2000`) to abort slow vector DB queries.
  - `OPENAI_EMBEDDING_TIMEOUT` and `OPENAI_EMBEDDING_MAX_RETRIES` default to `10` and `1` respectively.
- LLM timeout/retries: set `OPENAI_TIMEOUT` (e.g., `12`) and `OPENAI_MAX_RETRIES=0..1` for faster failure.
- Memory context size: pass `context_memory` in `/run` to limit past messages read (e.g., `50` loads the last 50 messages).
- Fallback writer: set `MEMORY_FALLBACK_WRITE=false` to avoid an extra DB write per turn (LangChain will still persist).

## Environment Variables (summary)
- Required at runtime:
  - `OPENAI_API_KEY` (or pass in run payload)
- Databases:
  - `DATABASE_URL` — main app DB (Prisma)
  - `KNOWLEDGE_DATABASE_URL` — knowledge/RAG DB (optional; defaults to `/knowledge_clevio_pro`)
  - `MEMORY_DATABASE_URL` — memory DB (recommended; e.g., `/memory_agent`)
- RAG:
  - `EMBEDDING_MODEL` (default `text-embedding-3-large`)
  - `RAG_LOG_CONTEXT`, `RAG_LOG_SYSTEM_MESSAGE`, `RAG_SNIPPET_PREVIEW_CHARS`
- Memory:
  - `MEMORY_FALLBACK_WRITE` (`true`|`false`) — enable/disable fallback writer
  - `MEMORY_DIR` — for file backend
- OpenAI client:
  - `OPENAI_TIMEOUT` (default 30), `OPENAI_MAX_RETRIES` (default 1)
- Response finalizer:
  - `FINALIZER_ENABLED` (`true`|`false`) controls the optional polishing pass (disabling saves an extra LLM call).
  - `FINALIZER_MODEL` overrides the lightweight model used for polishing.
  - `FAST_RAG_MODEL` / `FAST_RAG_TIMEOUT` / `FAST_RAG_MAX_TOKENS` configure the quick RAG summarizer when `FAST_RAG_RESPONSE=true`.
  - `FAST_RAG_LANGUAGE` forces the fast RAG summarizer to respond in a specific language (optional).

## Extending
- **Tools**: add a module under `agents/tools/` and register it in `agents/tools/registry.py`.
  Built-in tools include math evaluation (`calc`), numerous Google integrations (`google_search`, `google_serper`, `google_trends`, `google_places`, `google_finance`, `google_cloud_text_to_speech`, `google_jobs`, `google_scholar`, `google_books`, `google_lens`, `google_maps`, `gmail_search`, `gmail_read_messages`, `gmail_send_message`), an OpenAI-powered product lookup (`websearch`), and Google Sheets operations (`spreadsheet`).
- **Memory**: modify or extend `agents/memory.py`.

## Testing
```bash
pytest
```
- To bypass database lookups entirely, you can pass the full config in the run payload:
```bash
curl -X POST http://localhost:8000/agents/{agent_id}/run \
  -H 'Content-Type: application/json' \
  -d '{
        "message": "What is 2 + 2?",
        "config": {
          "model_name": "gpt-4o-mini",
          "system_message": "You are a helpful bot",
          "tools": ["calc"],
          "memory_enabled": false
        }
      }'
```
- Warm caches to speed up runs:
```bash
# Single agent
curl -X POST http://localhost:8000/agents/{agent_id}/warm

# All agents
curl -X POST http://localhost:8000/agents/warm_all
```

Bypassing the database on /run
- The run endpoint can be made DB-free for maximum responsiveness:
  - Preferred: warm the cache (as above). When `RUN_BYPASS_DB=true` (default), `/agents/{agent_id}/run` reads config from cache only.
    If a config is not cached, the API returns 400 with guidance to warm or pass `config` inline.
  - Or pass the full `config` with the run request so no cache or DB is required.
