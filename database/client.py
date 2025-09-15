import os
import json
import subprocess
from pathlib import Path
import time

from config.schema import AgentConfig
try:
    import psycopg2  # type: ignore
except Exception:  # pragma: no cover - optional in some deployments
    psycopg2 = None  # type: ignore

PRISMA_DIR = Path(__file__).resolve().parent / "prisma"
SCRIPT = PRISMA_DIR / "agent_service.js"
CACHE_DIR = Path(__file__).resolve().parent / "cache" / "agents"
OWNERS_DIR = Path(__file__).resolve().parent / "cache" / "owners"

# Optional: keep old behavior (migrate/generate) but only once per process
_AUTO_SYNC = os.getenv("PRISMA_AUTO_SYNC", "true").lower() == "true"
_SYNC_DONE = False
_CMD_TIMEOUT = float(os.getenv("PRISMA_CMD_TIMEOUT", "4"))  # seconds (short to fail fast)
_CACHE_TTL = float(os.getenv("AGENT_CACHE_TTL", "300"))  # seconds, 5 minutes default
_AGENT_CACHE: dict[str, tuple[AgentConfig, float]] = {}
_OWNER_CACHE: dict[str, str] = {}


def _with_connect_timeout(url: str, default_seconds: int = 3) -> str:
    try:
        if not url:
            return url
        # avoid double-adding
        if "connect_timeout=" in url:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}connect_timeout={default_seconds}"
    except Exception:
        return url


def _subprocess_env() -> dict:
    env = os.environ.copy()
    db_url = env.get("DATABASE_URL")
    if db_url:
        env["DATABASE_URL"] = _with_connect_timeout(db_url)
    return env


def _precheck_db():
    """Fail fast if the database is clearly unreachable.

    Uses psycopg2 with a tiny connect_timeout so we can return an immediate
    error instead of waiting for the Prisma engine to time out.
    """
    if psycopg2 is None:
        return
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return
    try:
        conn = psycopg2.connect(db_url, connect_timeout=2)
        conn.close()
    except Exception as exc:
        raise RuntimeError(
            "Cannot connect to database (fast precheck). Check DATABASE_URL and Postgres service."
        ) from exc


def _cache_path(agent_id: str) -> Path:
    return CACHE_DIR / f"{agent_id}.json"

def _owner_path(agent_id: str) -> Path:
    return OWNERS_DIR / f"{agent_id}.owner.json"


def _write_cached_config(agent_id: str, config: AgentConfig) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_cache_path(agent_id), "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, ensure_ascii=False)
    except Exception:
        pass

def _write_cached_owner(agent_id: str, owner_id: str) -> None:
    try:
        OWNERS_DIR.mkdir(parents=True, exist_ok=True)
        with open(_owner_path(agent_id), "w", encoding="utf-8") as f:
            json.dump({"owner_id": owner_id}, f)
    except Exception:
        pass


def _read_cached_config(agent_id: str) -> AgentConfig | None:
    try:
        path = _cache_path(agent_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AgentConfig(**data)
    except Exception:
        return None

def _read_cached_owner(agent_id: str) -> str | None:
    try:
        p = _owner_path(agent_id)
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        oid = data.get("owner_id")
        return str(oid) if oid is not None else None
    except Exception:
        return None


def _maybe_sync_prisma() -> None:
    global _SYNC_DONE
    if not _AUTO_SYNC or _SYNC_DONE:
        return
    # Best-effort: run migrate deploy and generate once, but don't block forever
    try:
        subprocess.run(
            ["npx", "prisma", "migrate", "deploy"],
            cwd=str(PRISMA_DIR),
            capture_output=True,
            check=True,
            timeout=_CMD_TIMEOUT,
        )
        subprocess.run(
            ["npx", "prisma", "generate"],
            cwd=str(PRISMA_DIR),
            capture_output=True,
            check=True,
            timeout=_CMD_TIMEOUT,
        )
        _SYNC_DONE = True
    except Exception:
        # Do not hard-fail here; surface errors on actual DB call
        _SYNC_DONE = True


def _run(command: str, payload: dict) -> dict:
    _maybe_sync_prisma()
    # If DB is down, short-circuit quickly with a clear error
    _precheck_db()
    try:
        result = subprocess.run(
            ["node", str(SCRIPT), command],
            cwd=str(PRISMA_DIR),
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            timeout=_CMD_TIMEOUT,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Database operation timed out. Ensure PostgreSQL is reachable and reduce load, "
            "or increase PRISMA_CMD_TIMEOUT."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raw = (exc.stderr or "").strip()
        msg = raw or f"Node command failed: {exc.cmd}"
        # Friendlier hints for common Prisma errors
        if "P1001" in msg or "Can\'t reach database" in msg or "Can't reach database" in msg:
            msg += "\nHint: Check DATABASE_URL and that your Postgres server is running and accessible."
        if "Error in Prisma Client" in msg and "remote call" in msg.lower():
            msg += "\nHint: This often indicates the Prisma query engine failed to connect or crashed."
        raise RuntimeError(msg) from exc
    return json.loads(result.stdout)


def create_agent_record(owner_id: str, name: str, config: AgentConfig) -> str:
    data = _run(
        "create",
        {
            "ownerKey": owner_id,
            "name": name,
            "config": config.model_dump(exclude={"openai_api_key"}),
        },
    )
    agent_id = str(data["id"])  # BigInt stringified by node layer
    # Warm caches so immediate subsequent runs don't hit DB/Prisma
    try:
        _AGENT_CACHE[agent_id] = (config, time.time())
    except Exception:
        pass
    try:
        _write_cached_config(agent_id, config)
    except Exception:
        pass
    return agent_id


def get_agent_config(agent_id: str) -> AgentConfig:
    # Serve from cache if fresh
    now = time.time()
    cached = _AGENT_CACHE.get(agent_id)
    if cached and (now - cached[1] <= _CACHE_TTL):
        return cached[0]

    # Try file cache first to avoid Prisma hits entirely
    file_cfg = _read_cached_config(agent_id)
    if file_cfg is not None:
        _AGENT_CACHE[agent_id] = (file_cfg, now)
        return file_cfg

    try:
        data = _run("get", {"agent_id": agent_id})
    except Exception as exc:
        # Fallback to file cache or in-memory cache on Prisma errors
        file_cfg = _read_cached_config(agent_id)
        if file_cfg is not None:
            print(f"[WARN] Prisma get failed; using file cache for {agent_id}: {exc}")
            _AGENT_CACHE[agent_id] = (file_cfg, now)
            return file_cfg
        cached = _AGENT_CACHE.get(agent_id)
        if cached:
            print(f"[WARN] Prisma get failed; using in-memory cache for {agent_id}: {exc}")
            return cached[0]
        raise

    # Map new DB fields to AgentConfig
    tools_list: list[str]
    raw_tools = data.get("tools")
    if isinstance(raw_tools, list):
        tools_list = raw_tools
    else:
        # stored as string (JSON); fallback to comma-split if plain text
        try:
            tools_list = list(json.loads(raw_tools or "[]"))
        except Exception:
            tools_list = [t.strip() for t in str(raw_tools or "").split(",") if t.strip()]

    # Memory defaults (DB schema may not store these fields)
    mem_enabled_default = os.getenv("AGENT_MEMORY_ENABLED_DEFAULT", "true").lower() == "true"
    mem_backend_default = os.getenv("AGENT_MEMORY_BACKEND_DEFAULT", "sql").lower()
    if mem_backend_default not in {"sql", "in_memory", "file"}:
        mem_backend_default = "sql"

    payload = {
        "model_name": data.get("nama_model"),
        "system_message": data.get("system_message"),
        "tools": tools_list,
        # Default to SQL-backed memory so conversations persist
        "memory_enabled": mem_enabled_default,
        "memory_backend": mem_backend_default,
    }
    if data.get("agent_type") is not None:
        payload["agent_type"] = data["agent_type"]
    cfg = AgentConfig(**payload)
    _AGENT_CACHE[agent_id] = (cfg, now)
    _write_cached_config(agent_id, cfg)
    return cfg


def list_agents_raw() -> list[dict]:
    """Return raw agent rows from the database (best-effort).

    Falls back to an empty list on failure; callers can still use file cache.
    """
    try:
        data = _run("list", {})
        # Old versions may return an object with `agents`
        if isinstance(data, dict) and "agents" in data:
            return list(data["agents"] or [])
        if isinstance(data, list):
            return data
        return []
    except Exception as exc:
        print(f"[WARN] list_agents_raw failed: {exc}")
        return []


def warm_cache_for_agent(agent_id: str) -> AgentConfig:
    """Ensure file + memory cache populated for a specific agent.

    Returns the resolved AgentConfig (from DB or cache).
    """
    cfg = get_agent_config(agent_id)
    _write_cached_config(agent_id, cfg)
    return cfg


def warm_cache_for_all() -> dict:
    """Warm caches for all agents in the database.

    Uses a single list query to avoid N+1, then serializes configs directly.
    Returns a small stats dict.
    """
    rows = list_agents_raw()
    warmed = 0
    skipped = 0
    errors = 0
    now = time.time()
    for row in rows:
        try:
            agent_id = str(row.get("id"))
            if not agent_id:
                continue
            # Parse tools
            tools_list: list[str]
            raw_tools = row.get("tools")
            if isinstance(raw_tools, list):
                tools_list = raw_tools
            else:
                try:
                    tools_list = list(json.loads(raw_tools or "[]"))
                except Exception:
                    tools_list = [t.strip() for t in str(raw_tools or "").split(",") if t.strip()]

            mem_enabled_default = os.getenv("AGENT_MEMORY_ENABLED_DEFAULT", "true").lower() == "true"
            mem_backend_default = os.getenv("AGENT_MEMORY_BACKEND_DEFAULT", "sql").lower()
            if mem_backend_default not in {"sql", "in_memory", "file"}:
                mem_backend_default = "sql"
            payload = {
                "model_name": row.get("nama_model"),
                "system_message": row.get("system_message"),
                "tools": tools_list,
                "memory_enabled": mem_enabled_default,
                "memory_backend": mem_backend_default,
            }
            if row.get("agent_type") is not None:
                payload["agent_type"] = row.get("agent_type")
            cfg = AgentConfig(**payload)
            _AGENT_CACHE[agent_id] = (cfg, now)
            _write_cached_config(agent_id, cfg)
            warmed += 1
        except Exception:
            errors += 1
    return {"warmed": warmed, "skipped": skipped, "errors": errors, "total": len(rows)}


def save_agent_google_token(agent_id: str, email: str, token: dict) -> None:
    """Save or update a unified Google OAuth token for an agent into list_account.

    Upserts on (user_id, agent_id, email) inside the Node layer.
    """
    _run(
        "save_token",
        {
            "agent_id": agent_id,
            "email": email,
            "servicesaccount": token,
        },
    )


def get_agent_owner_id(agent_id: str) -> str | None:
    """Return the numeric owner/user id for an agent when available.

    Works with both the new schema (field `user_id`) and the legacy (`ownerId`).
    Returns a string (BigInt or UUID), or None when not found.
    """
    # Fast path: in-memory cache
    oid = _OWNER_CACHE.get(agent_id)
    if oid:
        return oid
    # File cache
    oid_file = _read_cached_owner(agent_id)
    if oid_file:
        _OWNER_CACHE[agent_id] = oid_file
        return oid_file
    # Slow path: call Node process once, then cache
    try:
        data = _run("get", {"agent_id": agent_id})
        if isinstance(data, dict):
            oid = None
            if data.get("user_id") is not None:
                oid = str(data["user_id"])
            elif data.get("ownerId") is not None:
                oid = str(data["ownerId"])
            if oid:
                _OWNER_CACHE[agent_id] = oid
                _write_cached_owner(agent_id, oid)
                return oid
    except Exception:
        pass
    return None


def get_user_id_by_api_key(api_key: str) -> str | None:
    """Validate a presented API key against DB and return the user_id if valid.

    Relies on the Node/Prisma helper command `apikey_lookup` which matches
    by plaintext against the `key_hash` column (stores the plaintext key).
    Returns the user_id (string) when valid, else None.
    """
    try:
        data = _run("apikey_lookup", {"key": api_key})
        if isinstance(data, dict) and data.get("ok") and data.get("user_id") is not None:
            return str(data["user_id"])
    except Exception:
        pass
    return None


def create_api_key_for_user(user_id: str, label: str | None = None, expires_at: str | None = None, ttl_days: int | None = None) -> dict | None:
    """Create a new API key for a user and return details including the plaintext.

    Either provide `expires_at` (ISO date) or `ttl_days` (int). Returns a dict with
    keys: ok, id, user_id, expires_at, plaintext on success; None on failure.
    """
    payload: dict = {"user_id": user_id}
    if label is not None:
        payload["label"] = label
    if expires_at is not None:
        payload["expires_at"] = expires_at
    if ttl_days is not None:
        payload["ttl_days"] = int(ttl_days)
    try:
        data = _run("apikey_create", payload)
        if isinstance(data, dict) and data.get("ok"):
            return data
    except Exception:
        pass
    return None


def ensure_user(email: str, owner_key: str | None = None) -> str | None:
    """Ensure a user row exists for email and return its id (string)."""
    try:
        data = _run("ensure_user", {"email": email, "ownerKey": owner_key or email.split("@")[0]})
        if isinstance(data, dict) and data.get("ok") and data.get("user_id") is not None:
            return str(data["user_id"])
    except Exception:
        pass
    return None


def get_cached_agent_config(agent_id: str) -> AgentConfig | None:
    """Return config from in-memory or file cache only (no DB calls)."""
    cached = _AGENT_CACHE.get(agent_id)
    if cached:
        cfg = cached[0]
        # Enforce default memory behavior when cache was created with older defaults
        try:
            if not cfg.memory_enabled:
                mem_enabled_default = os.getenv("AGENT_MEMORY_ENABLED_DEFAULT", "true").lower() == "true"
                if mem_enabled_default:
                    cfg.memory_enabled = True
                    cfg.memory_backend = os.getenv("AGENT_MEMORY_BACKEND_DEFAULT", "sql").lower()
        except Exception:
            pass
        return cfg
    file_cfg = _read_cached_config(agent_id)
    if file_cfg is not None:
        try:
            _AGENT_CACHE[agent_id] = (file_cfg, time.time())
        except Exception:
            pass
        try:
            # Upgrade cached file configs to current memory defaults if disabled
            if not file_cfg.memory_enabled and os.getenv("AGENT_MEMORY_ENABLED_DEFAULT", "true").lower() == "true":
                file_cfg.memory_enabled = True
                file_cfg.memory_backend = os.getenv("AGENT_MEMORY_BACKEND_DEFAULT", "sql").lower()
        except Exception:
            pass
        return file_cfg
    return None
