import os
import logging
from typing import List, Tuple, Optional, Any

import psycopg2
from psycopg2.extras import RealDictCursor

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


def _connect_knowledge() -> Optional[Any]:
    url = _derive_knowledge_url()
    if not url:
        return None
    try:
        # Short connect timeout to avoid blocking
        conn = psycopg2.connect(url, connect_timeout=3)
        # Avoid long-running transactions for read-only similarity queries
        try:
            conn.autocommit = True
        except Exception:
            pass
        return conn
    except Exception:
        logger.warning("[RAG] connection to knowledge DB failed; skipping RAG")
        return None


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
    emb = OpenAIEmbeddings(model=model_name, api_key=key)
    try:
        v = emb.embed_query(text)
        logger.info(f"[RAG] embedded query with model={model_name}, dim={len(v)}")
        return v
    except Exception as e:
        logger.warning(f"[RAG] embedding failed: {e}")
        return None


def _table_name(user_id: str, agent_id: str) -> str:
    uid = "".join([c for c in str(user_id) if c.isdigit()])
    aid = "".join([c for c in str(agent_id) if c.isdigit()])
    return f"tb_{uid}{aid}"


def retrieve_topk(
    user_id: str,
    agent_id: str,
    query: str,
    top_k: int = 5,
    api_key: Optional[str] = None,
) -> List[dict]:
    """Return top-k rows from the agent's knowledge table ordered by vector similarity.

    Each row dict contains: id (uuid), text (str), metadata (dict|None), score (float distance)
    """
    logger.info(f"[RAG] start retrieval user_id={user_id} agent_id={agent_id} top_k={top_k}")
    # Prefer large embeddings by default for compatibility with 3072-dim pgvector columns
    primary_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
    vec = _embed_query(query, api_key, model=primary_model)
    if not vec:
        logger.info("[RAG] no embedding; retrieval skipped")
        return []
    conn = _connect_knowledge()
    if not conn:
        logger.info("[RAG] no knowledge DB connection; retrieval skipped")
        return []
    try:
        tbl = _table_name(user_id, agent_id)
        logger.info(f"[RAG] querying table=public.\"{tbl}\"")
        # Build vector string literal and bind as a parameter so psycopg2 quotes it
        vec_str = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
        sql = f"""
            SELECT id, text, metadata, embedding <-> (%s)::vector AS score
            FROM public."{tbl}"
            ORDER BY embedding <-> (%s)::vector
            LIMIT %s
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, max(1, int(top_k))))
            rows = cur.fetchall() or []
        logger.info(f"[RAG] retrieved {len(rows)} rows from {tbl}")
        # Convert metadata to dict if json string
        out = []
        for r in rows:
            md = r.get("metadata")
            out.append({
                "id": r.get("id"),
                "text": r.get("text"),
                "metadata": md,
                "score": r.get("score"),
            })
        return out
    except Exception as e:
        msg = str(e)
        logger.warning(f"[RAG] retrieval failed for table {tbl}: {msg}")
        # Auto-retry with alternate model on vector dimension mismatch (e.g., 3072 vs 1536)
        if "different vector dimensions" in msg or "vector dimensions" in msg:
            # Ensure the connection is usable before retrying
            try:
                conn.rollback()
            except Exception:
                pass
            alt = "text-embedding-3-large" if primary_model != "text-embedding-3-large" else "text-embedding-3-small"
            logger.info(f"[RAG] retrying with alternate embedding model: {alt}")
            vec = _embed_query(query, api_key, model=alt)
            if not vec:
                return []
            try:
                vec_str = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
                sql = f"""
                    SELECT id, text, metadata, embedding <-> (%s)::vector AS score
                    FROM public."{tbl}"
                    ORDER BY embedding <-> (%s)::vector
                    LIMIT %s
                """
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, (vec_str, vec_str, max(1, int(top_k))))
                    rows = cur.fetchall() or []
                logger.info(f"[RAG] retrieved {len(rows)} rows from {tbl} after retry")
                out = []
                for r in rows:
                    md = r.get("metadata")
                    out.append({
                        "id": r.get("id"),
                        "text": r.get("text"),
                        "metadata": md,
                        "score": r.get("score"),
                    })
                return out
            except Exception as e2:
                logger.warning(f"[RAG] retry retrieval failed: {e2}")
                return []
        return []
    finally:
        try:
            conn.close()
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
