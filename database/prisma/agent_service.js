const { PrismaClient } = require('@prisma/client');
const { randomUUID } = require('crypto');
const crypto = require('crypto');

const prisma = new PrismaClient();
let kprisma = null; // Lazy knowledge DB client
let mprisma = null; // Lazy memory DB client

function jsonBigInt(obj) {
  return JSON.stringify(obj, (_k, v) => (typeof v === 'bigint' ? v.toString() : v));
}

function _deriveKnowledgeUrl() {
  const envUrl = process.env.KNOWLEDGE_DATABASE_URL;
  if (envUrl && String(envUrl).trim()) {
    const s = String(envUrl).trim();
    // Ensure a short connect_timeout so failing hosts don't hang
    return s.includes('connect_timeout=') ? s : (s + (s.includes('?') ? '&' : '?') + 'connect_timeout=3');
  }
  // Fallback: replace DB name in DATABASE_URL with 'knowledge_clevio_pro'
  try {
    const base = process.env.DATABASE_URL;
    if (!base) return null;
    const u = new URL(base);
    // Pathname begins with '/'
    u.pathname = '/knowledge_clevio_pro';
    let out = u.toString();
    out += out.includes('?') ? '&connect_timeout=3' : '?connect_timeout=3';
    return out;
  } catch {
    return null;
  }
}

function _deriveMemoryUrl() {
  const envUrl = process.env.MEMORY_DATABASE_URL;
  if (envUrl && String(envUrl).trim()) {
    const s = String(envUrl).trim();
    return s.includes('connect_timeout=') ? s : (s + (s.includes('?') ? '&' : '?') + 'connect_timeout=3');
  }
  // Fallback: replace DB name in DATABASE_URL with 'memory_agent'
  try {
    const base = process.env.DATABASE_URL;
    if (!base) return null;
    const u = new URL(base);
    u.pathname = '/memory_agent';
    let out = u.toString();
    out += out.includes('?') ? '&connect_timeout=3' : '?connect_timeout=3';
    return out;
  } catch {
    return null;
  }
}

async function getKnowledgeClient() {
  if (kprisma) return kprisma;
  const url = _deriveKnowledgeUrl();
  if (!url) throw new Error('Knowledge DB URL not configured');
  // Override datasource URL at runtime
  kprisma = new PrismaClient({ datasources: { db: { url } } });
  return kprisma;
}

async function getMemoryClient() {
  if (mprisma) return mprisma;
  const url = _deriveMemoryUrl();
  if (!url) throw new Error('Memory DB URL not configured');
  mprisma = new PrismaClient({ datasources: { db: { url } } });
  return mprisma;
}

async function createKnowledgeTable(userId, agentId) {
  try {
    const uid = String(userId);
    const aid = String(agentId);
    // Keep digits only to form table suffix, per example tb_12
    const uidDigits = uid.replace(/\D/g, '');
    const aidDigits = aid.replace(/\D/g, '');
    const tableName = `tb_${uidDigits}${aidDigits}`;
    const kp = await getKnowledgeClient();
    // Ensure extensions in the knowledge DB (best-effort)
    try { await kp.$executeRaw`CREATE EXTENSION IF NOT EXISTS vector`; } catch {}
    try { await kp.$executeRaw`CREATE EXTENSION IF NOT EXISTS pgcrypto`; } catch {}
    try { await kp.$executeRaw`CREATE EXTENSION IF NOT EXISTS "uuid-ossp"`; } catch {}

    // Create table in knowledge DB public schema.
    // Use pgvector with explicit 3072 dimension to match text-embedding-3-large
    try {
      await kp.$executeRawUnsafe(
        `CREATE TABLE IF NOT EXISTS public."${tableName}" (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          text text NOT NULL,
          metadata jsonb,
          embedding vector(3072)
        )`
      );
      // Best-effort: add ANN index for faster similarity search
      try {
        await kp.$executeRawUnsafe(
          `CREATE INDEX IF NOT EXISTS "${tableName}_embedding_idx" ON public."${tableName}" USING ivfflat (embedding vector_l2_ops)`
        );
      } catch {}
    } catch (_e) {
      await kp.$executeRawUnsafe(
        `CREATE TABLE IF NOT EXISTS public."${tableName}" (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          text text NOT NULL,
          metadata jsonb,
          embedding vector(3072)
        )`
      );
      try {
        await kp.$executeRawUnsafe(
          `CREATE INDEX IF NOT EXISTS "${tableName}_embedding_idx" ON public."${tableName}" USING ivfflat (embedding vector_l2_ops)`
        );
      } catch {}
    }
    return { ok: true, table: tableName };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function createMemoryTable(userId, agentId) {
  try {
    const uid = String(userId);
    const aid = String(agentId);
    const uidDigits = uid.replace(/\D/g, '');
    const aidDigits = aid.replace(/\D/g, '');
    const tableName = `memory_${uidDigits}${aidDigits}`;
    const mp = await getMemoryClient();
    // Create simple chat history table per agent
    await mp.$executeRawUnsafe(
      `CREATE TABLE IF NOT EXISTS public."${tableName}" (
        id serial PRIMARY KEY,
        session_id character varying(255) NOT NULL,
        message text NOT NULL
      )`
    );
    // Helpful index on session_id
    try {
      await mp.$executeRawUnsafe(
        `CREATE INDEX IF NOT EXISTS "${tableName}_session_idx" ON public."${tableName}" (session_id)`
      );
    } catch {}
    // If table exists from previous versions with jsonb column, coerce to text to match LangChain
    try {
      await mp.$executeRawUnsafe(
        `ALTER TABLE public."${tableName}" ALTER COLUMN message TYPE text USING message::text`
      );
    } catch {}
    return { ok: true, table: tableName };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  const data = Buffer.concat(chunks).toString();
  return JSON.parse(data);
}

async function getUsersTableColumns() {
  try {
    const rows = await prisma.$queryRaw`SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='users'`;
    return new Set((rows || []).map((r) => String(r.column_name)));
  } catch {
    return new Set();
  }
}

async function ensureUserAndGetId(ownerKey, email) {
  // If ownerKey is numeric, treat it as an existing user id
  if (/^\d+$/.test(String(ownerKey))) {
    try { return BigInt(ownerKey); } catch {}
  }
  // Try new snake_case users table with dynamic columns
  const cols = await getUsersTableColumns();
  if (cols.size > 0) {
    try {
      // Prefer lookup by email if available, else by name
      if (cols.has('email')) {
        const found = await prisma.$queryRaw`SELECT id FROM "public"."users" WHERE "email" = ${email} LIMIT 1`;
        if (found && found[0] && found[0].id != null) return BigInt(found[0].id);
      } else if (cols.has('name') || cols.has('nama')) {
        const col = cols.has('name') ? 'name' : 'nama';
        const found = await prisma.$queryRawUnsafe(`SELECT id FROM "public"."users" WHERE "${col}" = $1 LIMIT 1`, String(ownerKey));
        if (found && found[0] && found[0].id != null) return BigInt(found[0].id);
      }
      // Insert with whatever columns we have (email/name)
      const insertCols = [];
      const params = [];
      if (cols.has('email')) { insertCols.push('"email"'); params.push(email); }
      if (cols.has('name')) { insertCols.push('"name"'); params.push(String(ownerKey)); }
      else if (cols.has('nama')) { insertCols.push('"nama"'); params.push(String(ownerKey)); }
      let sql;
      if (insertCols.length > 0) {
        const valuesPlaceholders = params.map((_, i) => `$${i + 1}`).join(',');
        sql = `INSERT INTO "public"."users" (${insertCols.join(',')}) VALUES (${valuesPlaceholders}) RETURNING "id";`;
        const ins = await prisma.$queryRawUnsafe(sql, ...params);
        if (ins && ins[0] && ins[0].id != null) return BigInt(ins[0].id);
      } else {
        // Fallback default insert if no known columns
        const def = await prisma.$queryRaw`INSERT INTO "public"."users" DEFAULT VALUES RETURNING "id";`;
        if (def && def[0] && def[0].id != null) return BigInt(def[0].id);
      }
    } catch {
      // fall through to legacy
    }
  }
  // Legacy PascalCase User table
  try {
    const found = await prisma.$queryRaw`SELECT "id" FROM "public"."User" WHERE "email" = ${email} LIMIT 1`;
    if (found && found[0] && found[0].id) return String(found[0].id);
  } catch {}
  try {
    const _uuid = (typeof randomUUID === 'function') ? randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const ins = await prisma.$queryRaw`INSERT INTO "public"."User" ("id", "email", "name", "createdAt") VALUES (${_uuid}, ${email}, ${String(ownerKey)}, NOW()) RETURNING "id";`;
    if (ins && ins[0] && ins[0].id) return String(ins[0].id);
  } catch {}
  throw new Error('Unable to create or find user in either users or "User" table');
}

async function main() {
  const command = process.argv[2];
  const payload = await readStdin();
  // Bound best-effort background tasks to prevent long hangs on slow/remote DBs
  const TABLE_TASK_TIMEOUT_MS = parseInt(process.env.TABLE_TASK_TIMEOUT_MS || '2000', 10);
  const withTimeout = async (promise) => {
    return Promise.race([
      promise,
      new Promise((resolve) => setTimeout(() => resolve({ ok: false, reason: 'timeout' }), TABLE_TASK_TIMEOUT_MS)),
    ]).catch(() => ({ ok: false }));
  };

  if (command === 'create') {
    // Ensure a user exists; derive by email from provided ownerKey
    const ownerKey = payload.ownerKey || payload.ownerId || payload.user_id || 'user';
    const email = `${ownerKey}@example.com`;
    // Try to ensure user exists in whatever schema is present; return id
    let ensuredUserId;
    try {
      ensuredUserId = await ensureUserAndGetId(ownerKey, email);
    } catch (e) {
      // As a last fallback try Prisma user model
      try {
        const u = await prisma.user.upsert({ where: { email }, update: {}, create: { email, name: String(ownerKey) } });
        ensuredUserId = (typeof u.id === 'bigint') ? u.id : (u.id ? BigInt(u.id) : undefined);
      } catch {
        throw e;
      }
    }

    const toolsValue = Array.isArray(payload.config?.tools)
      ? JSON.stringify(payload.config.tools)
      : JSON.stringify([]);

    let agent;
    // First, try inserting into the new snake_case table directly (uses ensured user_id)
    try {
      const userId = ensuredUserId;
      const insertAgentRows = await prisma.$queryRaw`INSERT INTO "public"."agent" ("user_id", "nama_model", "system_message", "tools", "agent_type") VALUES (${userId}, ${payload.config?.model_name}, ${payload.config?.system_message}, ${toolsValue}, ${payload.config?.agent_type || 'chat-conversational-react-description'}) RETURNING "id","user_id","nama_model","system_message","tools","agent_type","created_at","updated_at";`;
      agent = insertAgentRows?.[0];
      // Best-effort table creation with short timeouts (can be disabled via env)
      const ENABLE_CREATE = String(process.env.CREATE_TABLES_ON_AGENT_CREATE || 'true').toLowerCase() !== 'false';
      if (ENABLE_CREATE) {
        try { await withTimeout(createKnowledgeTable(userId, agent && agent.id)); } catch {}
        try { await withTimeout(createMemoryTable(userId, agent && agent.id)); } catch {}
      }
    } catch (rawErr) {
      try {
        // Fallback: legacy PascalCase tables via raw SQL
        // Ensure legacy user exists or reuse ensured numeric owner id when possible
        let legacyUserId;
        if (/^\d+$/.test(String(ownerKey))) {
          legacyUserId = String(ownerKey);
        } else {
          const upsertLegacyUser = await prisma.$queryRaw`INSERT INTO "public"."User" ("id", "email", "name", "createdAt")
            VALUES (gen_random_uuid()::text, ${email}, ${String(ownerKey)}, NOW())
            ON CONFLICT ("email") DO UPDATE SET "name" = EXCLUDED."name"
            RETURNING "id";`;
          legacyUserId = String((upsertLegacyUser?.[0] || {}).id);
        }
        const nameVal = payload.name || String(ownerKey);
        // Prepare tools array literal safely
        const toolsArray = Array.isArray(payload.config?.tools) ? payload.config.tools : [];
        const toolsArrayLiteral = `ARRAY[${toolsArray.map((t)=>`'${String(t).replace(/'/g, "''")}'`).join(',')}]::text[]`;
        const sql = `INSERT INTO "public"."Agent" ("ownerId", "name", "modelName", "systemMessage", "tools", "memoryEnabled", "memoryBackend", "agentType", "maxIterations", "maxExecutionTime")
          VALUES ($1, $2, $3, $4, ${toolsArray.length ? toolsArrayLiteral : 'ARRAY[]::text[]'}, $5, $6, $7, $8, $9)
          RETURNING "id";`;
        const params = [
          legacyUserId,
          nameVal,
          payload.config?.model_name,
          payload.config?.system_message,
          payload.config?.memory_enabled ?? false,
          payload.config?.memory_backend || 'in_memory',
          payload.config?.agent_type || 'chat-conversational-react-description',
          payload.config?.max_iterations ?? null,
          payload.config?.max_execution_time ?? null,
        ];
        const insertLegacy = await prisma.$queryRawUnsafe(sql, ...params);
        agent = insertLegacy?.[0] || { id: (insertLegacy && insertLegacy.id) };
        // Attempt to create knowledge table if IDs are numeric
        const uid = /^\d+$/.test(String(legacyUserId)) ? legacyUserId : null;
        const aid = agent && agent.id && /^\d+$/.test(String(agent.id)) ? agent.id : null;
        if (uid && aid) {
          const ENABLE_CREATE = String(process.env.CREATE_TABLES_ON_AGENT_CREATE || 'true').toLowerCase() !== 'false';
          if (ENABLE_CREATE) {
            try { await withTimeout(createKnowledgeTable(uid, aid)); } catch {}
            try { await withTimeout(createMemoryTable(uid, aid)); } catch {}
          }
        }
      } catch (legacyRawErr) {
        // No compatible DB schema detected. Surface a clear error.
        const hint =
          'Agent create failed: database schema not initialized. '
          + 'Create table public.agent or legacy public."Agent" and their relations.';
        const details = `new_raw_error=${String(rawErr)}; legacy_raw_error=${String(legacyRawErr)}`;
        throw new Error(`${hint}\n${details}`);
      }
    }
    console.log(jsonBigInt(agent));
  } else if (command === 'get') {
    let agent = null;
    // 1) Try Prisma (new schema)
    try {
      const id = payload.agent_id ? BigInt(payload.agent_id) : undefined;
      agent = await prisma.agent.findUnique({ where: { id } });
    } catch (_) {}
    // 2) Raw SQL against new snake_case table
    if (!agent) {
      try {
        const rows = await prisma.$queryRawUnsafe(
          `SELECT id, user_id, nama_model, system_message, tools, agent_type
           FROM "public"."agent"
           WHERE id = CAST($1 AS bigint)
           LIMIT 1`,
          String(payload.agent_id)
        );
        if (rows && rows[0]) agent = rows[0];
      } catch (_) {}
    }
    // 3) Raw SQL legacy PascalCase table
    if (!agent) {
      try {
        const rows = await prisma.$queryRawUnsafe(
          `SELECT "id", "ownerId", "modelName", "systemMessage", "tools", "agentType"
           FROM "public"."Agent"
           WHERE "id" = $1
           LIMIT 1`,
          String(payload.agent_id)
        );
        if (rows && rows[0]) agent = rows[0];
      } catch (_) {}
    }
    console.log(jsonBigInt(agent || {}));
  } else if (command === 'list') {
    const agents = await prisma.agent.findMany({});
    console.log(jsonBigInt(agents));
  } else if (command === 'apikey_lookup') {
    try {
      const key = String(payload.key || '');
      if (!key) {
        console.log(jsonBigInt({ ok: false, reason: 'missing_key' }));
        return;
      }
      const hash = crypto.createHash('sha256').update(key).digest('hex');
      const rows = await prisma.$queryRaw`SELECT user_id, active, expires_at FROM "public"."api_key" WHERE key_hash = ${hash} LIMIT 1`;
      const row = rows && rows[0];
      if (!row) {
        console.log(jsonBigInt({ ok: false, reason: 'not_found' }));
        return;
      }
      const active = !!row.active;
      const exp = row.expires_at ? new Date(row.expires_at) : null;
      const now = new Date();
      if (!active || (exp && now > exp)) {
        console.log(jsonBigInt({ ok: false, reason: 'expired_or_inactive' }));
        return;
      }
      // Update last_used_at best-effort
      try {
        await prisma.$queryRaw`UPDATE "public"."api_key" SET last_used_at = NOW() WHERE key_hash = ${hash}`;
      } catch {}
      console.log(jsonBigInt({ ok: true, user_id: row.user_id }));
    } catch (e) {
      console.log(jsonBigInt({ ok: false, reason: 'lookup_failed', error: String(e) }));
    }
  } else if (command === 'apikey_create') {
    try {
      const userId = payload.user_id;
      if (!userId) {
        console.log(jsonBigInt({ ok: false, reason: 'missing_user_id' }));
        return;
      }
      let plaintext = String(payload.plaintext || '').trim();
      if (!plaintext) {
        plaintext = crypto.randomBytes(32).toString('hex');
      }
      const hash = crypto.createHash('sha256').update(plaintext).digest('hex');
      const label = (payload.label != null) ? String(payload.label) : null;
      let expiresAt = null;
      if (payload.expires_at) {
        try { expiresAt = new Date(String(payload.expires_at)); } catch {}
      } else if (payload.ttl_days) {
        const days = parseInt(String(payload.ttl_days), 10);
        if (!isNaN(days) && days > 0) {
          const d = new Date();
          d.setUTCDate(d.getUTCDate() + days);
          expiresAt = d;
        }
      }
      const rows = await prisma.$queryRaw`INSERT INTO "public"."api_key" ("user_id", "key_hash", "label", "expires_at", "active") VALUES (${BigInt(userId)}, ${hash}, ${label}, ${expiresAt}, TRUE) RETURNING id, user_id, expires_at`;
      const row = rows && rows[0];
      console.log(jsonBigInt({ ok: true, id: row && row.id, user_id: row && row.user_id, expires_at: row && row.expires_at, plaintext }));
    } catch (e) {
      console.log(jsonBigInt({ ok: false, reason: 'create_failed', error: String(e) }));
    }
  } else if (command === 'ensure_user') {
    try {
      const email = String(payload.email || '').trim();
      if (!email) {
        console.log(jsonBigInt({ ok: false, reason: 'missing_email' }));
        return;
      }
      const ownerKey = (payload.ownerKey != null) ? String(payload.ownerKey) : (email.split('@')[0] || 'user');
      const id = await ensureUserAndGetId(ownerKey, email);
      console.log(jsonBigInt({ ok: true, user_id: id }));
    } catch (e) {
      console.log(jsonBigInt({ ok: false, reason: 'ensure_user_failed', error: String(e) }));
    }
  } else if (command === 'save_token') {
    // Upsert a token into list_account for the agent
    try {
      const agentIdBig = BigInt(payload.agent_id);
      const agent = await prisma.agent.findUnique({ where: { id: agentIdBig } });
      if (!agent) throw new Error('Agent not found');
      const userIdBig = payload.user_id ? BigInt(payload.user_id) : BigInt(agent.user_id);
      const email = String(payload.email || `${payload.agent_id}@googleuser.local`).toLowerCase();
      const token = payload.servicesaccount || payload.token || {};
      const tokenJson = JSON.stringify(token);

      // Try an explicit SELECT + UPDATE/INSERT so it works without unique indexes
      const existing = await prisma.$queryRaw`SELECT id FROM "public"."list_account" WHERE "user_id" = ${userIdBig} AND "agent_id" = ${agentIdBig} AND "email" = ${email} LIMIT 1`;
      if (existing && existing[0] && existing[0].id != null) {
        const updated = await prisma.$queryRaw`UPDATE "public"."list_account" SET "servicesaccount" = ${tokenJson}::jsonb, "updated_at" = NOW() WHERE id = ${existing[0].id} RETURNING id`;
        console.log(jsonBigInt({ ok: true, action: 'updated', id: updated && updated[0] && updated[0].id }));
      } else {
        const inserted = await prisma.$queryRaw`INSERT INTO "public"."list_account" ("user_id", "agent_id", "email", "servicesaccount") VALUES (${userIdBig}, ${agentIdBig}, ${email}, ${tokenJson}::jsonb) RETURNING id`;
        console.log(jsonBigInt({ ok: true, action: 'inserted', id: inserted && inserted[0] && inserted[0].id }));
      }
    } catch (e) {
      // Surface error but don't crash the caller by exiting silently
      console.log(jsonBigInt({ ok: false, reason: 'save_token_failed', error: String(e) }));
    }
  }
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
    try { if (kprisma) await kprisma.$disconnect(); } catch {}
    try { if (mprisma) await mprisma.$disconnect(); } catch {}
  });
