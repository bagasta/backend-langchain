const { PrismaClient } = require('@prisma/client');
const { randomUUID } = require('crypto');

const prisma = new PrismaClient();

function jsonBigInt(obj) {
  return JSON.stringify(obj, (_k, v) => (typeof v === 'bigint' ? v.toString() : v));
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
    let agent;
    try {
      // Try BigInt id (new schema)
      const id = payload.agent_id ? BigInt(payload.agent_id) : undefined;
      agent = await prisma.agent.findUnique({ where: { id } });
    } catch (e) {
      try {
        // Fallback: string id (legacy schema)
        agent = await prisma.agent.findUnique({ where: { id: String(payload.agent_id) } });
      } catch (_e) {
        throw e;
      }
    }
    console.log(jsonBigInt(agent));
  } else if (command === 'list') {
    const agents = await prisma.agent.findMany({});
    console.log(jsonBigInt(agents));
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
  });
