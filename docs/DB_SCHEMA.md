# Database Structure

The application uses PostgreSQL via Prisma for core entities. Conversation memory can optionally use a separate SQL store managed by LangChain (not part of Prisma).

## Prisma Models

Source: `database/prisma/schema.prisma`

### User (`@@map("users")`)
- `id` (BigInt, @id, @default(autoincrement())) — primary key
- `email` (String, @unique)
- `name` (String)
- `agents` (Agent[]) — one‑to‑many relationship
- `created_at` (DateTime, @default(now()), timestamptz)
- `updated_at` (DateTime, @updatedAt, timestamptz)

### Agent (`@@map("agent")`)
- `id` (BigInt, @id, @default(autoincrement())) — primary key
- `user_id` (BigInt) — foreign key to `User.id`, `onDelete: Cascade`
- `user` (User @relation)
- `nama_model` (String) — e.g., `gpt-4o-mini`
- `system_message` (String) — agent’s base/system prompt
- `tools` (String) — stored as string (JSON string recommended)
- `agent_type` (String)
- `created_at` (DateTime, @default(now()), timestamptz)
- `updated_at` (DateTime, @updatedAt, timestamptz)
- Index: `idx_agent_user_id` on (`user_id`)

### ListAccount (`@@map("list_account")`)
- `id` (BigInt, @id, @default(autoincrement())) — primary key
- `user_id` (BigInt) — FK → `users.id` (cascade)
- `agent_id` (BigInt) — FK → `agent.id` (cascade)
- `email` (String) — Google account email
- `servicesaccount` (JsonB) — unified Google OAuth token JSON
- `created_at` (DateTime, @default(now()), timestamptz)
- `updated_at` (DateTime, @updatedAt, timestamptz)
- Indexes: `idx_list_account_user_id`, `idx_list_account_agent_id`
- Unique: `uq_list_account_user_agent_email` on (`user_id`, `agent_id`, `email`)

Persisted fields are written by `database/prisma/agent_service.js` during `create` operations; `openai_api_key` is never persisted. Tokens for Google are saved per‑agent into `list_account` via the universal OAuth callback.

## Creation Flow

When `POST /agents/` is called:
- Ensures a `User` row exists for the provided `owner_id` key (resolved to `email=<owner_id>@example.com` if not numeric).
- Writes an `Agent` row with `nama_model`, `system_message`, `tools` (JSON string), and `agent_type`.
- Returns the new `Agent.id` and a unified `auth_urls.google` for one‑time Google OAuth covering Gmail/Calendar/Docs.

## Caching (Non‑DB)

To reduce DB load and allow DB‑free runs:
- In‑memory cache: `database/client.py` keeps recent `AgentConfig` instances for `AGENT_CACHE_TTL` seconds.
- File cache: `database/cache/agents/{agent_id}.json` stores serialized configs for resilience across restarts.
- `/agents/{id}/warm` and `/agents/warm_all` populate these caches.

## Conversation Memory (Optional)

When `memory_backend` is:
- `sql`: Conversation history is stored via LangChain’s `SQLChatMessageHistory` using `DATABASE_URL` (SQLAlchemy). Tables are created automatically by LangChain; these are separate from Prisma models.
- `file`: Per‑agent histories are written as JSON files under `MEMORY_DIR`.
- `in_memory`: History exists only for the current process.
