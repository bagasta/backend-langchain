How to Run Without Docker (Local/Server)

This guide shows how to run the API directly on a server or your machine without Docker, using a Python venv and Uvicorn. It also covers Prisma migrations against your existing Postgres.

Prerequisites
- Python 3.10+ (3.12 recommended)
- Node.js 18+ and npm (required for the Prisma Node helper)
- Access to your existing PostgreSQL server and credentials
- Optional: psql client for quick DB checks

1) Clone and create a virtualenv
- git clone <this repo> && cd <repo>
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -U pip
- pip install -r requirements.txt

2) Configure environment variables (.env)
- Copy or edit `.env` at repo root. At minimum set:
  - `OPENAI_API_KEY=...`
  - `DATABASE_URL=postgresql://<user>:<pass>@<host>:5432/cleviopro`
  - `KNOWLEDGE_DATABASE_URL=postgresql://<user>:<pass>@<host>:5432/knowledge_clevio_pro`
  - `MEMORY_DATABASE_URL=postgresql://<user>:<pass>@<host>:5432/memory_agent`
  - `API_KEYS=bootstrap-123@2025-12-01`  (bootstrap admin key for testing)
  - Optional (remote DBs can be slow): `PRISMA_CMD_TIMEOUT=15`

Notes
- The app loads `.env` at startup (see `main.py`:1). No need to export manually.
- If `KNOWLEDGE_DATABASE_URL` or `MEMORY_DATABASE_URL` are omitted, the code derives them from `DATABASE_URL` by replacing the DB name (see `database/prisma/agent_service.js`:1).

3) Install Node dependencies for Prisma helper
- cd database/prisma
- npm ci --no-audit --no-fund
- cd ../..

4) Prepare databases on your Postgres server (one-time)
- Ensure three databases exist (names can be changed in your .env):
  - cleviopro (main)
  - knowledge_clevio_pro (RAG)
  - memory_agent (chat memory)
- Ensure required extensions are available on the knowledge and memory DBs (if your role can install extensions):
  - CREATE EXTENSION IF NOT EXISTS vector;
  - CREATE EXTENSION IF NOT EXISTS pgcrypto;
  - CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
  If your role cannot create extensions, ask your DBA to install them.

5) Run Prisma migrations against your existing DB
- Show current status:
  - npx prisma migrate status --schema database/prisma/schema.prisma
- If you see a failed legacy migration (P3009) like `20250901000000_add_agent_fields`, resolve it (these legacy migrations targeted PascalCase tables that your DB likely doesn’t use):
  - npx prisma migrate resolve --schema database/prisma/schema.prisma --rolled-back 20250804093746_init
  - npx prisma migrate resolve --schema database/prisma/schema.prisma --rolled-back 20250901000000_add_agent_fields
  - npx prisma migrate resolve --schema database/prisma/schema.prisma --rolled-back 20250901000001_add_memory_backend
- Apply current migrations (idempotent; create snake_case tables if missing):
 - npx prisma migrate deploy --schema database/prisma/schema.prisma
 - npx prisma generate --schema database/prisma/schema.prisma

The relevant schema for tables is in `database/prisma/schema.prisma`. The code paths that depend on these tables are in `database/prisma/agent_service.js` and `database/client.py`.

6) Start the API with Uvicorn
- From repo root (venv active):
  - uvicorn main:app --host 0.0.0.0 --port 8000
- Optional (auto-reload for dev): add `--reload`

7) Test the API
- Health check:
  - curl -i http://localhost:8000/
  - curl -i http://localhost:8000/healthz
- Generate an API key (bootstrap auth via header):
  - curl -sS -X POST http://localhost:8000/api_keys/generate \
    -H 'X-API-Key: bootstrap-123' \
    -H 'Content-Type: application/json' \
    -d '{"email":"admin@example.com","label":"server","ttl_days":365}'
  Response should include `{ ok: true, key: "..." }`.

8) Optional: Using agents and RAG
- The server defaults make it easy to test without committing writes on runs:
  - `RUN_BYPASS_DB=true` means `/agents/{id}/run` uses cached config; warm cache via `/agents/{id}/warm` or provide config inline. See `router/agents.py`:1 and `docs/API_REFERENCE.md`:175.
  - Set `RAG_ENABLED=false` if you don’t want retrieval. See `agents/rag.py`:118.

Troubleshooting
- 500 on /api_keys/generate
  - Check server logs printed by Uvicorn; failed DB calls bubble up as 500.
  - Re-run Prisma steps above (status/resolve/deploy). Ensure tables `public.users`, `public.agent`, `public.list_account`, and `public.api_key` exist.
  - Permissions: the DB role must INSERT/SELECT on `public.users` and `public.api_key`.
  - Slow remote DB: increase `PRISMA_CMD_TIMEOUT` (e.g., 15-30).
  - If running under systemd, ensure Node is installed and visible to the service; set `NODE_BIN=/usr/bin/node` in the unit if needed.
- Directly test the Prisma helper (helpful error messages):
  - Ensure user: `node database/prisma/agent_service.js ensure_user <<< '{"email":"admin@example.com"}'`
  - Create key: `node database/prisma/agent_service.js apikey_create <<< '{"user_id":"<ID>","label":"server","ttl_days":365}'`
- Connectivity
  - If Uvicorn logs show "Cannot connect to database" early, verify `DATABASE_URL` and firewall, and that Postgres allows your host (pg_hba.conf).

Production notes
- Use a process supervisor (systemd) to run Uvicorn as a service.
- Keep your `.env` secure and rotate the bootstrap key in `API_KEYS` after you’ve created per-user keys.
 - Health endpoints: `/` and `/healthz` support GET/HEAD, useful for load balancers and uptime checks.
