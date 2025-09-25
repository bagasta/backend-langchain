import os
import logging
import time
import hashlib
import json
import threading
from typing import List, Optional, Any, Sequence, Tuple, Dict
from dataclasses import dataclass
from collections import OrderedDict

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2 import pool
except Exception:  # pragma: no cover - optional dependency
    psycopg2 = None  # type: ignore
    RealDictCursor = None  # type: ignore
    pool = None  # type: ignore

try:
    from langchain_openai import OpenAIEmbeddings
except Exception:  # pragma: no cover
    OpenAIEmbeddings = None  # type: ignore

try:
    import redis
except Exception:  # pragma: no cover - optional dependency
    redis = None  # type: ignore


logger = logging.getLogger("rag")
if not logger.handlers:
    # Inherit root config; set level from env (default INFO)
    level = os.getenv("RAG_LOG_LEVEL", "INFO").upper()
    try:
        logger.setLevel(getattr(logging, level, logging.INFO))
    except Exception:
        logger.setLevel(logging.INFO)


def _derive_knowledge_url() -> Optional[str]:
    url = os.getenv("KNOWLEDGE_DATABASE_URL")
    if url:
        try:
            # Log DB name without credentials
            from urllib.parse import urlparse
            p = urlparse(url)
            logger.info(f"[RAG] knowledge DB set via env: //{p.hostname}:{p.port}/{(p.path or '/')[1:]}")
        except Exception:
            pass
        return url
    base = os.getenv("DATABASE_URL")
    if not base:
        return None
    try:
        from urllib.parse import urlparse, urlunparse

        p = urlparse(base)
        newurl = urlunparse((p.scheme, p.netloc, "/knowledge_clevio_pro", "", "", ""))
        logger.info(f"[RAG] knowledge DB derived from DATABASE_URL → knowledge_clevio_pro")
        return newurl
    except Exception:
        return None


_KNOWLEDGE_CONN = None
_CONNECTION_POOL = None

def _get_redis_client():
    """Get Redis client for distributed caching."""
    global _redis_client

    # If Redis is not configured, return None gracefully
    if not redis or not _REDIS_URL:
        return None

    if _redis_client is None:
        try:
            _redis_client = redis.from_url(_REDIS_URL)
            # Test connection
            _redis_client.ping()
            logger.info("[RAG] Redis client initialized successfully")
        except Exception as e:
            logger.warning(f"[RAG] Redis connection failed: {e}")
            _redis_client = None
    return _redis_client

def _get_connection_pool():
    """Get or create connection pool for vector operations."""
    global _CONNECTION_POOL
    if _CONNECTION_POOL is None and psycopg2 and pool:
        try:
            url = _derive_knowledge_url()
            if url:
                _CONNECTION_POOL = pool.ThreadedConnectionPool(
                    minconn=_CONNECTION_POOL_MIN,
                    maxconn=_CONNECTION_POOL_MAX,
                    dsn=url
                )
                logger.info(f"[RAG] Connection pool initialized ({_CONNECTION_POOL_MIN}-{_CONNECTION_POOL_MAX})")
        except Exception as e:
            logger.warning(f"[RAG] Connection pool creation failed: {e}")
            _CONNECTION_POOL = None
    return _CONNECTION_POOL

def _connect_knowledge() -> Optional[Any]:
    """Get connection from pool or create single connection as fallback."""
    # Try connection pool first
    conn_pool = _get_connection_pool()
    if conn_pool:
        try:
            conn = conn_pool.getconn(_CONNECTION_POOL_TIMEOUT)
            # Ensure autocommit for read-only queries
            try:
                conn.autocommit = True
            except Exception:
                pass
            return conn
        except Exception as e:
            logger.warning(f"[RAG] Connection pool get failed: {e}")

    # Fallback to single connection
    url = _derive_knowledge_url()
    if not url:
        return None
    if psycopg2 is None:
        return None
    try:
        global _KNOWLEDGE_CONN
        if _KNOWLEDGE_CONN is not None and getattr(_KNOWLEDGE_CONN, "closed", 1) == 0:
            return _KNOWLEDGE_CONN
        # Short connect timeout to avoid blocking
        conn = psycopg2.connect(url, connect_timeout=1)
        # Avoid long-running transactions for read-only similarity queries
        try:
            conn.autocommit = True
        except Exception:
            pass
        _KNOWLEDGE_CONN = conn
        return conn
    except Exception:
        logger.warning("[RAG] connection to knowledge DB failed; skipping RAG")
        return None

def _release_connection(conn: Optional[Any]) -> None:
    """Release connection back to pool or close it."""
    if conn is None:
        return

    conn_pool = _get_connection_pool()
    if conn_pool and hasattr(conn_pool, 'putconn'):
        try:
            conn_pool.putconn(conn)
        except Exception:
            # If putting back to pool fails, try to close the connection
            try:
                conn.close()
            except Exception:
                pass
    else:
        # Close single connection
        try:
            conn.close()
        except Exception:
            pass

# Performance tracking and configuration
@dataclass
class RAGMetrics:
    query_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    avg_query_time: float = 0.0
    total_query_time: float = 0.0

_metrics = RAGMetrics()
_metrics_lock = threading.Lock()

# Track which tables we've optimized (index/ANALYZE) to avoid repeated DDL
_OPTIMIZED_TABLES: set[str] = set()
_TABLE_VECTOR_DIM_CACHE: Dict[str, Optional[int]] = {}

# Enhanced caching configuration
_EMBED_CACHE_TTL = float(os.getenv("RAG_EMBED_CACHE_TTL_SECONDS", "900"))
_EMBED_CACHE_MAX = int(os.getenv("RAG_EMBED_CACHE_MAX", "1024"))  # Increased from 512
_EMBED_CACHE: OrderedDict[Tuple[str, str], Tuple[float, List[float]]] = OrderedDict()

# Query result caching
_QUERY_CACHE_TTL = float(os.getenv("RAG_QUERY_CACHE_TTL_SECONDS", "300"))
_QUERY_CACHE_MAX = int(os.getenv("RAG_QUERY_CACHE_MAX", "1000"))
_QUERY_CACHE: OrderedDict[str, Tuple[float, List[dict]]] = OrderedDict()

# Connection pooling
_CONNECTION_POOL_MIN = int(os.getenv("RAG_CONNECTION_POOL_MIN", "2"))
_CONNECTION_POOL_MAX = int(os.getenv("RAG_CONNECTION_POOL_MAX", "10"))
_CONNECTION_POOL_TIMEOUT = float(os.getenv("RAG_CONNECTION_POOL_TIMEOUT", "30"))

# Index configuration
_IVFFLAT_MAX_DIM = int(os.getenv("RAG_IVFFLAT_MAX_DIM", "2000"))
_HNSW_ENABLED = os.getenv("RAG_HNSW_ENABLED", "true").lower() == "true"
_HNSW_M = int(os.getenv("RAG_HNSW_M", "16"))
_HNSW_EF_CONSTRUCTION = int(os.getenv("RAG_HNSW_EF_CONSTRUCTION", "64"))
_HNSW_EF_SEARCH = int(os.getenv("RAG_HNSW_EF_SEARCH", "40"))

# Adaptive configuration
_ADAPTIVE_PROBES = os.getenv("RAG_ADAPTIVE_PROBES", "true").lower() == "true"
_BASE_PROBES = int(os.getenv("RAG_IVFFLAT_PROBES", "1"))
_MAX_PROBES = int(os.getenv("RAG_MAX_PROBES", "10"))

# Performance settings
_FULLSCAN_TIMEOUT_MS = int(os.getenv("RAG_FULLSCAN_TIMEOUT_MS", "15000"))
_STATEMENT_TIMEOUT_MS = int(os.getenv("RAG_STATEMENT_TIMEOUT_MS", "5000"))
_PARALLEL_QUERIES = int(os.getenv("RAG_PARALLEL_QUERIES", "1"))

# Redis configuration for distributed caching
_REDIS_URL = os.getenv("RAG_REDIS_URL")
_REDIS_TTL = int(os.getenv("RAG_REDIS_TTL", "3600"))
_redis_client = None

def _parse_model_dim_hints() -> Dict[str, int]:
    hints: Dict[str, int] = {
        "text-embedding-3-large": 3072,
        "text-embedding-3-large-v1": 3072,
        "text-embedding-3-small": 1536,
        "text-embedding-3-small-v1": 1536,
        "text-embedding-ada-002": 1536,
    }
    raw = os.getenv("RAG_MODEL_DIM_HINTS")
    if not raw:
        return hints
    for entry in raw.split(","):
        if not entry:
            continue
        try:
            name, value = entry.split("=", 1)
            dim = int(value.strip())
            if dim > 0:
                hints[name.strip()] = dim
        except Exception:
            continue
    return hints

_MODEL_DIM_HINTS = _parse_model_dim_hints()


def _dim_for_model(model_name: Optional[str]) -> Optional[int]:
    if not model_name:
        return None
    return _MODEL_DIM_HINTS.get(model_name)


def _distance_to_similarity(distance: Any) -> Optional[float]:
    """Convert cosine distance (0..2) to similarity (1..-1).

    Returns None when distance cannot be interpreted as a float.
    """

    if distance is None:
        return None
    try:
        similarity = 1.0 - float(distance)
    except (TypeError, ValueError):
        return None
    # Cosine similarity is theoretically in [-1, 1]. Clamp gently to avoid
    # noisy values from numerical error.
    if similarity > 1.0:
        similarity = 1.0
    elif similarity < -1.0:
        similarity = -1.0
    return similarity


def _get_query_cache_key(query: str, user_id: str, agent_id: str, top_k: int, model: str) -> str:
    """Generate a cache key for query results."""
    key_data = {
        "query": query.strip(),
        "user_id": user_id,
        "agent_id": agent_id,
        "top_k": top_k,
        "model": model
    }
    return hashlib.md5(json.dumps(key_data, sort_keys=True).encode()).hexdigest()

def _get_cached_query_results(cache_key: str) -> Optional[List[dict]]:
    """Get cached query results from memory or Redis."""
    now = time.time()

    # Check memory cache first
    if cache_key in _QUERY_CACHE:
        timestamp, results = _QUERY_CACHE[cache_key]
        if now - timestamp <= _QUERY_CACHE_TTL:
            with _metrics_lock:
                _metrics.cache_hits += 1
            return results
        else:
            _QUERY_CACHE.pop(cache_key, None)

    # Check Redis cache
    redis_client = _get_redis_client()
    if redis_client:
        try:
            cached_data = redis_client.get(f"rag_query:{cache_key}")
            if cached_data:
                results = json.loads(cached_data)
                # Update memory cache
                _QUERY_CACHE[cache_key] = (now, results)
                with _metrics_lock:
                    _metrics.cache_hits += 1
                return results
        except Exception as e:
            logger.warning(f"[RAG] Redis cache get failed: {e}")

    with _metrics_lock:
        _metrics.cache_misses += 1
    return None

def _cache_query_results(cache_key: str, results: List[dict]) -> None:
    """Cache query results in memory and Redis."""
    now = time.time()

    # Update memory cache with LRU eviction
    _QUERY_CACHE[cache_key] = (now, results)
    if len(_QUERY_CACHE) > _QUERY_CACHE_MAX:
        _QUERY_CACHE.popitem(last=False)

    # Update Redis cache
    redis_client = _get_redis_client()
    if redis_client:
        try:
            redis_client.setex(
                f"rag_query:{cache_key}",
                _REDIS_TTL,
                json.dumps(results)
            )
        except Exception as e:
            logger.warning(f"[RAG] Redis cache set failed: {e}")

def _embed_query(text: str, api_key: Optional[str] = None, model: Optional[str] = None) -> Optional[List[float]]:
    if not text:
        return None
    if OpenAIEmbeddings is None:
        logger.info("[RAG] OpenAIEmbeddings not available; skip embedding")
        return None
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        logger.info("[RAG] OPENAI_API_KEY missing; skip embedding")
        return None

    # Default to OpenAI text-embedding-3-large (3072 dims) unless overridden
    model_name = model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
    cache_key = (model_name, text.strip())
    now = time.time()

    # Check embedding cache with LRU eviction
    if cache_key in _EMBED_CACHE:
        timestamp, embedding = _EMBED_CACHE[cache_key]
        if now - timestamp <= _EMBED_CACHE_TTL:
            logger.info("[RAG] embedding cache hit (model=%s)", model_name)
            with _metrics_lock:
                _metrics.cache_hits += 1
            return embedding
        else:
            _EMBED_CACHE.pop(cache_key, None)

    # Apply adaptive timeout and retry logic for better reliability
    try:
        timeout = float(os.getenv("OPENAI_EMBEDDING_TIMEOUT", "3"))  # Increased default timeout
    except Exception:
        timeout = 3.0
    timeout = max(timeout, 1.0)  # Ensure minimum timeout of 1s
    try:
        retries = int(os.getenv("OPENAI_EMBEDDING_MAX_RETRIES", "2"))  # Increased default retries
    except Exception:
        retries = 2

    # Create embeddings client without timeout (not supported in current version)
    emb = OpenAIEmbeddings(model=model_name, api_key=key)

    # Implement retry logic with exponential backoff
    max_retries = 3
    for attempt in range(max_retries):
        try:
            v = emb.embed_query(text)
            logger.info(f"[RAG] embedded query with model={model_name}, dim={len(v)}, attempt={attempt + 1}")

            # Cache embedding with LRU eviction
            if _EMBED_CACHE_MAX > 0:
                _EMBED_CACHE[cache_key] = (now, v)
                if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                    _EMBED_CACHE.popitem(last=False)

            with _metrics_lock:
                _metrics.cache_misses += 1
            return v

        except Exception as e:
            logger.warning(f"[RAG] embedding attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                # Last attempt failed, try with smaller model if available
                if model_name == "text-embedding-3-large":
                    logger.info("[RAG] trying fallback to smaller embedding model")
                    return _embed_query(text, api_key, model="text-embedding-3-small")
                logger.error(f"[RAG] embedding failed after {max_retries} attempts: {e}")
                return None
            # Exponential backoff before retry
            import time as time_module
            time_module.sleep(2 ** attempt)


def embed_text(text: str, api_key: Optional[str] = None, model: Optional[str] = None) -> Optional[List[float]]:
    """Public helper to embed arbitrary text using the configured embeddings provider."""

    return _embed_query(text, api_key=api_key, model=model)


def warm_rag_clients() -> None:
    """Warm OpenAI, Postgres, Redis clients and connection pool to avoid first-request latency."""

    try:
        if os.getenv("RAG_WARM_ON_START", "true").lower() != "true":
            return

        # Warm embedding cache
        sample_text = os.getenv("RAG_WARM_TEXT", "Warmup token")
        embed_text(sample_text)

        # Initialize connection pool
        conn_pool = _get_connection_pool()
        if conn_pool:
            logger.info("[RAG] Connection pool initialized successfully")

        # Test database connection
        conn = _connect_knowledge()
        if conn is not None:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

        # Test Redis connection if configured
        redis_client = _get_redis_client()
        if redis_client:
            logger.info("[RAG] Redis client initialized successfully")
        else:
            logger.info("[RAG] Redis not configured - using memory caching only")

        # Log configuration
        config = get_rag_configuration()
        logger.info(f"[RAG] Configuration: HNSW={config['hnsw_enabled']}, "
                   f"Adaptive Probes={config['adaptive_probes']}, "
                   f"Connection Pool={config['connection_pool_min']}-{config['connection_pool_max']}")

    except Exception as exc:
        logger.warning("[RAG] warmup failed: %s", exc)


def _table_name(user_id: str, agent_id: str) -> str:
    uid = "".join([c for c in str(user_id) if c.isdigit()])
    aid = "".join([c for c in str(agent_id) if c.isdigit()])
    return f"tb_{uid}_{aid}"


def _table_vector_dim(conn: Any, table: str) -> Optional[int]:
    cached = _TABLE_VECTOR_DIM_CACHE.get(table)
    if cached is not None:
        return cached
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT atttypmod
                FROM pg_attribute
                WHERE attrelid = 'public."{table}"'::regclass
                  AND attname = 'embedding'
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row and isinstance(row[0], int) and row[0] > 0:
                raw = int(row[0])
                # pgvector stores the explicit dimension in atttypmod;
                # some builds include VARHDRSZ (4) in older versions. Detect both.
                if raw in _MODEL_DIM_HINTS.values():
                    dim = raw
                elif raw - 4 in _MODEL_DIM_HINTS.values():
                    dim = raw - 4
                else:
                    # Prefer raw when positive; fallback subtracting 4 if the result is sensible.
                    dim = raw if raw > 0 else None
                    if dim and dim not in _MODEL_DIM_HINTS.values() and raw - 4 > 0:
                        dim = raw - 4
                if dim and dim > 0:
                    _TABLE_VECTOR_DIM_CACHE[table] = dim
                    return dim
    except Exception:
        pass
    _TABLE_VECTOR_DIM_CACHE[table] = None
    return None


def _model_for_dimension(dim: int, prefer: Optional[str] = None) -> Optional[str]:
    if prefer and _MODEL_DIM_HINTS.get(prefer) == dim:
        return prefer
    for name, hint_dim in _MODEL_DIM_HINTS.items():
        if hint_dim == dim:
            return name
    return None


def _get_adaptive_probes(table_size: Optional[int] = None) -> int:
    """Calculate adaptive probe count based on table size."""
    if not _ADAPTIVE_PROBES:
        return _BASE_PROBES

    if table_size is None:
        return _BASE_PROBES

    # Adaptive probe calculation based on dataset size
    if table_size < 1000:
        return _BASE_PROBES
    elif table_size < 10000:
        return min(_BASE_PROBES + 2, _MAX_PROBES)
    elif table_size < 100000:
        return min(_BASE_PROBES + 4, _MAX_PROBES)
    else:
        return _MAX_PROBES

def _estimate_table_size(conn, table: str) -> Optional[int]:
    """Estimate table size for adaptive configuration."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM public.\"{table}\"")
            return cur.fetchone()[0]
    except Exception:
        return None

def _ensure_optimized_index(conn, table: str, table_dim: Optional[int]) -> str:
    """Ensure optimal index is created for the table."""
    index_type = "ivfflat"

    # Check dimension limits and choose appropriate index type
    if table_dim and table_dim > 2000:
        # For tables with >2000 dimensions, try to use partial indexes on vector subsets
        logger.info(f"[RAG] Table {table} has {table_dim} dimensions, using optimized sequential scan")
        return "sequential"

    # Use HNSW for high-dimensional vectors if enabled and within limits
    if _HNSW_ENABLED and (table_dim is None or (table_dim > 1536 and table_dim <= 2000)):
        index_type = "hnsw"

        if table not in _OPTIMIZED_TABLES:
            try:
                with conn.cursor() as cur:
                    # Create HNSW index
                    cur.execute(
                        f'CREATE INDEX IF NOT EXISTS "{table}_embedding_hnsw_idx" '
                        f'ON public."{table}" USING hnsw (embedding vector_cosine_ops) '
                        f'WITH (m = {_HNSW_M}, ef_construction = {_HNSW_EF_CONSTRUCTION})'
                    )

                    if os.getenv("RAG_ANALYZE", "true").lower() == "true":
                        cur.execute(f'ANALYZE public."{table}"')

                    logger.info(f"[RAG] created HNSW index for {table}")
                    _OPTIMIZED_TABLES.add(table)

            except Exception as e:
                logger.warning(f"[RAG] HNSW index creation failed for {table}: {e}")
                # Fall back to IVFFLAT
                index_type = "ivfflat"

    # Use IVFFLAT for lower dimensions or if HNSW fails
    if index_type == "ivfflat" and table not in _OPTIMIZED_TABLES:
        # Check if dimensions are within IVFFLAT limits
        if table_dim and table_dim > 2000:
            logger.warning(f"[RAG] Table {table} exceeds IVFFLAT dimension limit, using sequential scan")
            return "sequential"

        try:
            with conn.cursor() as cur:
                ops = os.getenv("RAG_INDEX_OPS", "vector_cosine_ops")
                cur.execute(
                    f'CREATE INDEX IF NOT EXISTS "{table}_embedding_idx" '
                    f'ON public."{table}" USING ivfflat (embedding {ops})'
                )

                if os.getenv("RAG_ANALYZE", "true").lower() == "true":
                    cur.execute(f'ANALYZE public."{table}"')

                logger.info(f"[RAG] created IVFFLAT index for {table}")
                _OPTIMIZED_TABLES.add(table)

        except Exception as e:
            logger.warning(f"[RAG] index creation failed for {table}: {e}")
            return "sequential"

    return index_type

def retrieve_topk(
    user_id: str,
    agent_id: str,
    query: Optional[str],
    top_k: int = 3,
    api_key: Optional[str] = None,
    query_vector: Optional[Sequence[float]] = None,
    metadata_filter: Optional[Dict[str, Any]] = None,
    batch_size: Optional[int] = None,
) -> List[dict]:
    """Return top-k rows ordered by cosine similarity to the provided query vector.

    Enhanced version with caching, connection pooling, and adaptive optimization.

    Each row dict contains: id (uuid), text (str), metadata (dict|None), score (cosine distance)
    and similarity (1 - distance when computable).
    """
    start_time = time.time()

    if os.getenv("RAG_ENABLED", "true").lower() != "true":
        logger.info("[RAG] disabled via RAG_ENABLED; skipping")
        return []

    try:
        top_k = max(1, int(top_k))
    except Exception:
        top_k = 3

    primary_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")

    # Check query cache first
    if query:
        cache_key = _get_query_cache_key(query, user_id, agent_id, top_k, primary_model)
        cached_results = _get_cached_query_results(cache_key)
        if cached_results:
            logger.info(f"[RAG] query cache hit for {cache_key[:16]}...")
            return cached_results[:top_k]

    logger.info(f"[RAG] start retrieval user_id={user_id} agent_id={agent_id} top_k={top_k}")

    # Configure batch size for large datasets
    if batch_size is None:
        batch_size = int(os.getenv("RAG_BATCH_SIZE", "100"))
    batch_size = max(10, min(batch_size, 1000))  # Reasonable bounds

    # Accept a pre-computed embedding (preferred) or embed inline as a fallback
    vec: Optional[List[float]] = None
    if query_vector is not None:
        try:
            vec = [float(x) for x in query_vector]
        except Exception as exc:
            logger.warning(f"[RAG] invalid query_vector provided: {exc}")

    if vec is None:
        if not query:
            logger.info("[RAG] no query text to embed; retrieval skipped")
            return []
        vec = _embed_query(query, api_key, model=primary_model)
        if not vec:
            logger.info("[RAG] embedding failed; retrieval skipped")
            return []
        if primary_model:
            _MODEL_DIM_HINTS.setdefault(primary_model, len(vec))

    conn = _connect_knowledge()
    if not conn:
        logger.info("[RAG] no knowledge DB connection; retrieval skipped")
        return []
    try:
        table_dim: Optional[int] = None
        tbl = _table_name(user_id, agent_id)
        logger.info(f"[RAG] querying table=public.\"{tbl}\"")
        table_dim = _table_vector_dim(conn, tbl)

        # Check vector dimension compatibility
        if vec is not None and table_dim is not None and len(vec) != table_dim:
            if query:
                alt_model = _model_for_dimension(table_dim, prefer=primary_model)
                if alt_model and alt_model != primary_model:
                    logger.info(
                        "[RAG] query vector dimension %s mismatches table (%s); re-embedding with %s",
                        len(vec),
                        table_dim,
                        alt_model,
                    )
                    new_vec = _embed_query(query, api_key, model=alt_model)
                    if new_vec:
                        vec = new_vec
                        _MODEL_DIM_HINTS.setdefault(alt_model, len(vec))
            if vec is None or len(vec) != table_dim:
                logger.warning(
                    "[RAG] table %s expects dimension %s but query vector has %s; skipping retrieval",
                    tbl,
                    table_dim,
                    0 if vec is None else len(vec),
                )
                return []

        # Use enhanced index creation with HNSW support
        index_type = _ensure_optimized_index(conn, tbl, table_dim)

        # Get adaptive configuration
        table_size = _estimate_table_size(conn, tbl)
        adaptive_probes = _get_adaptive_probes(table_size)
        # Build enhanced query with metadata filtering support
        vec_str = "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"

        # Add metadata filtering if provided
        where_clause = ""
        query_params = [vec_str, vec_str, max(1, int(top_k))]

        if metadata_filter:
            conditions = []
            for key, value in metadata_filter.items():
                if isinstance(value, str):
                    conditions.append(f"metadata->>'{key}' = %s")
                    query_params.append(value)
                elif isinstance(value, (int, float, bool)):
                    conditions.append(f"(metadata->>'{key}')::numeric = %s")
                    query_params.append(str(value))
                elif isinstance(value, dict):
                    conditions.append(f"metadata @> %s")
                    query_params.append(json.dumps({key: value}))

            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

        sql = f"""
            SELECT id, text, metadata, embedding <=> (%s)::vector AS score
            FROM public."{tbl}"
            {where_clause}
            ORDER BY embedding <=> (%s)::vector
            LIMIT %s
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Enhanced statement timeout with adaptive configuration
            try:
                timeout_ms = _STATEMENT_TIMEOUT_MS
                if index_type == "hnsw":
                    # HNSW is faster, can use shorter timeout
                    timeout_ms = min(timeout_ms, 1000)
                elif index_type == "sequential":
                    # Sequential scans for high-dimensional vectors need more time
                    timeout_ms = max(timeout_ms, _FULLSCAN_TIMEOUT_MS)
                elif not table_size or table_size > 100000:
                    # Large tables or unknown size need more time
                    timeout_ms = max(timeout_ms, _FULLSCAN_TIMEOUT_MS)

                cur.execute(f"SET statement_timeout TO {timeout_ms}")
            except Exception:
                pass

            # Enhanced index configuration
            try:
                if index_type == "hnsw":
                    # Configure HNSW search parameters
                    cur.execute(f"SET hnsw.ef_search = {_HNSW_EF_SEARCH}")
                elif index_type == "ivfflat":
                    # Configure IVFFLAT with adaptive probes
                    cur.execute(f"SET ivfflat.probes = {adaptive_probes}")
            except Exception:
                pass

            # Ensure index usage when available
            try:
                if index_type != "none" and os.getenv("RAG_DISABLE_SEQSCAN", "true").lower() == "true":
                    cur.execute("SET enable_seqscan TO off")
            except Exception:
                pass

            # Execute query with enhanced parameters
            cur.execute(sql, query_params)
            rows = cur.fetchall() or []
        logger.info(f"[RAG] retrieved {len(rows)} rows from {tbl} (index: {index_type}, probes: {adaptive_probes})")

        # Convert metadata to dict if json string
        out = []
        for r in rows:
            md = r.get("metadata")
            distance = r.get("score")
            similarity = _distance_to_similarity(distance)
            out.append(
                {
                    "id": r.get("id"),
                    "text": r.get("text"),
                    "metadata": md,
                    "score": distance,
                    "similarity": similarity,
                }
            )

        # Cache query results if successful
        if query and out:
            cache_key = _get_query_cache_key(query, user_id, agent_id, top_k, primary_model)
            _cache_query_results(cache_key, out)

        # Update performance metrics
        query_time = time.time() - start_time
        with _metrics_lock:
            _metrics.query_count += 1
            _metrics.total_query_time += query_time
            _metrics.avg_query_time = _metrics.total_query_time / _metrics.query_count

        logger.info(f"[RAG] query completed in {query_time:.3f}s (cached: {bool(query and out)})")

        return out
    except Exception as e:
        msg = str(e)
        logger.warning(f"[RAG] retrieval failed for table {tbl}: {msg}")
        # Auto-retry with alternate model on vector dimension mismatch (e.g., 3072 vs 1536)
        if ("different vector dimensions" in msg or "vector dimensions" in msg) and query:
            # Ensure the connection is usable before retrying
            try:
                conn.rollback()
            except Exception:
                pass
            alt_dim = table_dim or _dim_for_model(primary_model) or 0
            alt_model = None
            if alt_dim:
                alt_model = _model_for_dimension(alt_dim, prefer=None)
            if not alt_model:
                alt_model = "text-embedding-3-small" if primary_model == "text-embedding-3-large" else "text-embedding-3-large"
            logger.info(f"[RAG] retrying with alternate embedding model: {alt_model}")
            vec = _embed_query(query, api_key, model=alt_model)
            if not vec:
                return []
            _MODEL_DIM_HINTS.setdefault(alt_model, len(vec))
            try:
                vec_str = "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"
                sql = f"""
                    SELECT id, text, metadata, embedding <=> (%s)::vector AS score
                    FROM public."{tbl}"
                    ORDER BY embedding <=> (%s)::vector
                    LIMIT %s
                """
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    try:
                        timeout_ms = int(os.getenv("RAG_STATEMENT_TIMEOUT_MS", "2000"))
                        if not index_supported:
                            timeout_ms = max(timeout_ms, _FULLSCAN_TIMEOUT_MS)
                        cur.execute(f"SET statement_timeout TO {timeout_ms}")
                    except Exception:
                        pass
                    try:
                        if index_supported and os.getenv("RAG_DISABLE_SEQSCAN", "true").lower() == "true":
                            cur.execute("SET enable_seqscan TO off")
                    except Exception:
                        pass
                    try:
                        probes = int(os.getenv("RAG_IVFFLAT_PROBES", "1"))
                        cur.execute(f"SET ivfflat.probes = {probes}")
                    except Exception:
                        pass
                    cur.execute(sql, (vec_str, vec_str, max(1, int(top_k))))
                    rows = cur.fetchall() or []
                logger.info(f"[RAG] retrieved {len(rows)} rows from {tbl} after retry")
                out = []
                for r in rows:
                    md = r.get("metadata")
                    distance = r.get("score")
                    similarity = _distance_to_similarity(distance)
                    out.append(
                        {
                            "id": r.get("id"),
                            "text": r.get("text"),
                            "metadata": md,
                            "score": distance,
                            "similarity": similarity,
                        }
                    )
                return out
            except Exception as e2:
                logger.warning(f"[RAG] retry retrieval failed: {e2}")
                return []
        return []
    finally:
        # Properly release connection back to pool or close it
        _release_connection(conn)


def get_rag_metrics() -> Dict[str, Any]:
    """Get current RAG performance metrics."""
    with _metrics_lock:
        cache_hit_rate = (
            _metrics.cache_hits / (_metrics.cache_hits + _metrics.cache_misses)
            if (_metrics.cache_hits + _metrics.cache_misses) > 0
            else 0.0
        )

        return {
            "query_count": _metrics.query_count,
            "cache_hits": _metrics.cache_hits,
            "cache_misses": _metrics.cache_misses,
            "cache_hit_rate": cache_hit_rate,
            "avg_query_time": _metrics.avg_query_time,
            "total_query_time": _metrics.total_query_time,
            "embedding_cache_size": len(_EMBED_CACHE),
            "query_cache_size": len(_QUERY_CACHE),
            "optimized_tables": len(_OPTIMIZED_TABLES),
            "vector_dim_cache_size": len(_TABLE_VECTOR_DIM_CACHE),
        }

def reset_rag_metrics() -> None:
    """Reset RAG performance metrics."""
    with _metrics_lock:
        global _metrics
        _metrics = RAGMetrics()

def get_rag_configuration() -> Dict[str, Any]:
    """Get current RAG configuration."""
    return {
        "hnsw_enabled": _HNSW_ENABLED,
        "hnsw_m": _HNSW_M,
        "hnsw_ef_construction": _HNSW_EF_CONSTRUCTION,
        "hnsw_ef_search": _HNSW_EF_SEARCH,
        "adaptive_probes": _ADAPTIVE_PROBES,
        "base_probes": _BASE_PROBES,
        "max_probes": _MAX_PROBES,
        "connection_pool_min": _CONNECTION_POOL_MIN,
        "connection_pool_max": _CONNECTION_POOL_MAX,
        "embed_cache_ttl": _EMBED_CACHE_TTL,
        "embed_cache_max": _EMBED_CACHE_MAX,
        "query_cache_ttl": _QUERY_CACHE_TTL,
        "query_cache_max": _QUERY_CACHE_MAX,
        "redis_enabled": _REDIS_URL is not None,
        "parallel_queries": _PARALLEL_QUERIES,
        "batch_size": int(os.getenv("RAG_BATCH_SIZE", "100")),
    }

def format_context(snippets: List[dict]) -> str:
    if not snippets:
        return ""
    lines = []
    for i, s in enumerate(snippets, 1):
        tx = (s.get("text") or "").strip()
        if not tx:
            continue
        if len(tx) > 1000:
            tx = tx[:1000] + "..."
        lines.append(f"[{i}] {tx}")
    if not lines:
        return ""
    header = "Context from your knowledge base (most relevant first):"
    guidance = (
        "Use this context when answering. If irrelevant, ignore it."
    )
    return header + "\n" + "\n".join(lines) + "\n\n" + guidance


def retrieve_topk_batch(
    user_id: str,
    agent_id: str,
    queries: List[str],
    top_k: int = 3,
    api_key: Optional[str] = None,
    metadata_filter: Optional[Dict[str, Any]] = None,
    batch_size: Optional[int] = None,
) -> List[List[dict]]:
    """Batch retrieval for multiple queries to improve performance with large datasets.

    Args:
        user_id: User identifier
        agent_id: Agent identifier
        queries: List of query strings to process
        top_k: Number of results to return per query
        api_key: OpenAI API key
        metadata_filter: Optional metadata filtering
        batch_size: Number of queries to process in each batch

    Returns:
        List of result lists, one per input query
    """
    if not queries:
        return []

    # Configure batch processing
    if batch_size is None:
        batch_size = int(os.getenv("RAG_BATCH_SIZE", "50"))
    batch_size = max(1, min(batch_size, 100))  # Reasonable bounds for query batching

    results = []
    primary_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")

    logger.info(f"[RAG] processing {len(queries)} queries in batches of {batch_size}")

    # Process queries in batches
    for i in range(0, len(queries), batch_size):
        batch = queries[i:i + batch_size]
        logger.info(f"[RAG] processing batch {i//batch_size + 1}/{(len(queries)-1)//batch_size + 1}")

        # Embed all queries in the batch efficiently
        batch_vectors = []
        for query in batch:
            if not query:
                batch_vectors.append(None)
                continue

            # Check cache first
            cache_key = _get_query_cache_key(query, user_id, agent_id, top_k, primary_model)
            cached = _get_cached_query_results(cache_key)
            if cached:
                results.append(cached[:top_k])
                batch_vectors.append(None)  # Mark as already processed
                continue

            # Embed query
            vec = _embed_query(query, api_key, model=primary_model)
            batch_vectors.append(vec)

        # Process non-cached queries
        for j, (query, vec) in enumerate(zip(batch, batch_vectors)):
            if vec is None:  # Skip cached queries
                continue

            # Use standard retrieve_topk with pre-computed vector
            query_results = retrieve_topk(
                user_id=user_id,
                agent_id=agent_id,
                query=query,
                top_k=top_k,
                api_key=api_key,
                query_vector=vec,
                metadata_filter=metadata_filter,
            )

            results.append(query_results)

            # Cache results
            cache_key = _get_query_cache_key(query, user_id, agent_id, top_k, primary_model)
            _cache_query_results(cache_key, query_results)

    logger.info(f"[RAG] batch processing completed for {len(queries)} queries")
    return results
