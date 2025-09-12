-- Initialize application databases and extensions

-- Ensure default app DB exists (created automatically via POSTGRES_DB)
\connect cleviopro;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Knowledge DB
DO $$ BEGIN
  PERFORM 1 FROM pg_database WHERE datname = 'knowledge_clevio_pro';
  IF NOT FOUND THEN
    EXECUTE 'CREATE DATABASE knowledge_clevio_pro';
  END IF;
END $$;

\connect knowledge_clevio_pro;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Memory DB
DO $$ BEGIN
  PERFORM 1 FROM pg_database WHERE datname = 'memory_agent';
  IF NOT FOUND THEN
    EXECUTE 'CREATE DATABASE memory_agent';
  END IF;
END $$;

\connect memory_agent;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

