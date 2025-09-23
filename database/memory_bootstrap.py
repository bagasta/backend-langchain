import os
import logging
from urllib.parse import urlparse

try:
    import psycopg2
except Exception:  # pragma: no cover - optional dependency
    psycopg2 = None  # type: ignore


log = logging.getLogger("memory_bootstrap")


def _parse_base_conn_info(url: str):
    """Return (admin_dsn, target_db_name) for creating the target DB.

    Builds an admin DSN pointing to the server's default 'postgres' database
    using the same host/port/user/password from the given URL.
    """
    p = urlparse(url)
    # Remove leading '/'
    target_db = (p.path or "/")[1:] or "postgres"
    # Build admin DSN to default 'postgres' DB on the same server
    netloc = p.netloc
    scheme = p.scheme
    admin_dsn = f"{scheme}://{netloc}/postgres"
    return admin_dsn, target_db


def ensure_memory_database():
    """Ensure the memory database exists if MEMORY_DATABASE_URL is set.

    This function is best-effort: it logs failures and returns without raising,
    so the app can still start using in-memory history as a fallback.
    """
    url = os.getenv("MEMORY_DATABASE_URL")
    if not url:
        return
    if psycopg2 is None:
        return
    try:
        admin_dsn, dbname = _parse_base_conn_info(url)
        # Connect to admin DB and check/create target DB
        conn = psycopg2.connect(admin_dsn, connect_timeout=3)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
                exists = cur.fetchone() is not None
                if not exists:
                    log.info("[MEM] creating database %s", dbname)
                    cur.execute(f"CREATE DATABASE \"{dbname}\" ENCODING 'UTF8' TEMPLATE template0")
                else:
                    log.info("[MEM] database %s already exists", dbname)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:  # pragma: no cover - external DB state
        log.warning("[MEM] unable to ensure memory DB: %s", e)

