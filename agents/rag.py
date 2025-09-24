import os
import logging
import time
from typing import List, Optional, Any, Sequence, Tuple, Dict

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:  # pragma: no cover - optional dependency
    psycopg2 = None  # type: ignore
    RealDictCursor = None  # type: ignore

try:
    from langchain_openai import OpenAIEmbeddings
except Exception:  # pragma: no cover
    OpenAIEmbeddings = None  # type: ignore


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


def _connect_knowledge() -> Optional[Any]:
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

# Track which tables we've optimized (index/ANALYZE) to avoid repeated DDL
_OPTIMIZED_TABLES: set[str] = set()
_TABLE_VECTOR_DIM_CACHE: Dict[str, Optional[int]] = {}
_EMBED_CACHE_TTL = float(os.getenv("RAG_EMBED_CACHE_TTL_SECONDS", "900"))
_EMBED_CACHE_MAX = int(os.getenv("RAG_EMBED_CACHE_MAX", "512"))
_EMBED_CACHE: dict[Tuple[str, str], Tuple[float, List[float]]] = {}
_IVFFLAT_MAX_DIM = int(os.getenv("RAG_IVFFLAT_MAX_DIM", "2000"))
_FULLSCAN_TIMEOUT_MS = int(os.getenv("RAG_FULLSCAN_TIMEOUT_MS", "8000"))

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
    cached = _EMBED_CACHE.get(cache_key)
    if cached and now - cached[0] <= _EMBED_CACHE_TTL:
        logger.info("[RAG] embedding cache hit (model=%s)", model_name)
        return cached[1]
    # Apply shorter timeout and fewer retries for snappy RAG
    try:
        timeout = float(os.getenv("OPENAI_EMBEDDING_TIMEOUT", "1"))
    except Exception:
        timeout = 1.0
    timeout = min(timeout, 1.0)
    try:
        retries = int(os.getenv("OPENAI_EMBEDDING_MAX_RETRIES", "0"))
    except Exception:
        retries = 0
    emb = OpenAIEmbeddings(model=model_name, api_key=key, timeout=timeout, max_retries=retries)
    try:
        v = emb.embed_query(text)
        logger.info(f"[RAG] embedded query with model={model_name}, dim={len(v)}")
        if _EMBED_CACHE_MAX > 0:
            if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
                oldest_key = min(_EMBED_CACHE.items(), key=lambda item: item[1][0])[0]
                _EMBED_CACHE.pop(oldest_key, None)
            _EMBED_CACHE[cache_key] = (now, v)
        return v
    except Exception as e:
        logger.warning(f"[RAG] embedding failed: {e}")
        return None


def embed_text(text: str, api_key: Optional[str] = None, model: Optional[str] = None) -> Optional[List[float]]:
    """Public helper to embed arbitrary text using the configured embeddings provider."""

    return _embed_query(text, api_key=api_key, model=model)


def warm_rag_clients() -> None:
    """Warm OpenAI and Postgres clients to avoid first-request latency."""

    try:
        if os.getenv("RAG_WARM_ON_START", "true").lower() != "true":
            return
        sample_text = os.getenv("RAG_WARM_TEXT", "Warmup token")
        embed_text(sample_text)
        conn = _connect_knowledge()
        if conn is not None:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
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


def retrieve_topk(
    user_id: str,
    agent_id: str,
    query: Optional[str],
    top_k: int = 3,
    api_key: Optional[str] = None,
    query_vector: Optional[Sequence[float]] = None,
) -> List[dict]:
    """Return top-k rows ordered by cosine similarity to the provided query vector.

    Each row dict contains: id (uuid), text (str), metadata (dict|None), score (cosine distance)
    and similarity (1 - distance when computable).
    """

    if os.getenv("RAG_ENABLED", "true").lower() != "true":
        logger.info("[RAG] disabled via RAG_ENABLED; skipping")
        return []
    try:
        top_k = max(1, int(top_k))
    except Exception:
        top_k = 3
    logger.info(
        f"[RAG] start retrieval user_id={user_id} agent_id={agent_id} top_k={top_k}"
    )

    primary_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")

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
        index_supported = table_dim is None or table_dim <= _IVFFLAT_MAX_DIM
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
        # One-time best-effort index/ANALYZE to keep ANN fast on existing tables
        try:
            if (
                index_supported
                and os.getenv("RAG_ENSURE_INDEX", "true").lower() == "true"
                and tbl not in _OPTIMIZED_TABLES
            ):
                with conn.cursor() as _cur:
                    ops = os.getenv("RAG_INDEX_OPS", "vector_cosine_ops")
                    try:
                        _cur.execute(
                            f"CREATE INDEX IF NOT EXISTS \"{tbl}_embedding_idx\" ON public.\"{tbl}\" USING ivfflat (embedding {ops})"
                        )
                    except Exception as index_exc:
                        logger.warning("[RAG] index ensure failed for %s: %s", tbl, index_exc)
                    if os.getenv("RAG_ANALYZE", "true").lower() == "true":
                        try:
                            _cur.execute(f"ANALYZE public.\"{tbl}\"")
                        except Exception as analyze_exc:
                            logger.warning("[RAG] analyze failed for %s: %s", tbl, analyze_exc)
                _OPTIMIZED_TABLES.add(tbl)
            elif not index_supported and tbl not in _OPTIMIZED_TABLES:
                logger.info(
                    "[RAG] skipping IVFFLAT index for %s (dimension %s exceeds limit %s)",
                    tbl,
                    table_dim,
                    _IVFFLAT_MAX_DIM,
                )
                _OPTIMIZED_TABLES.add(tbl)
        except Exception as _e:  # pragma: no cover - permissions or races
            logger.warning("[RAG] index preparation failed: %s", _e)
        # Build vector string literal and bind as a parameter so psycopg2 quotes it
        vec_str = "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"
        sql = f"""
            SELECT id, text, metadata, embedding <=> (%s)::vector AS score
            FROM public."{tbl}"
            ORDER BY embedding <=> (%s)::vector
            LIMIT %s
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Statement timeout to fail fast on slow remote queries
            try:
                timeout_ms = int(os.getenv("RAG_STATEMENT_TIMEOUT_MS", "2000"))
                if not index_supported:
                    timeout_ms = max(timeout_ms, _FULLSCAN_TIMEOUT_MS)
                cur.execute(f"SET statement_timeout TO {timeout_ms}")
            except Exception:
                pass
            # Ensure ANN index is used aggressively only when an index exists
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
        logger.info(f"[RAG] retrieved {len(rows)} rows from {tbl}")
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
        try:
            if getattr(conn, "closed", 0):
                globals()["_KNOWLEDGE_CONN"] = None
        except Exception:
            pass


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
