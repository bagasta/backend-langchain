-- Create api_key table for per-user API keys (hashed)
DO $$ BEGIN
    CREATE TABLE IF NOT EXISTS public.api_key (
        id           BIGSERIAL PRIMARY KEY,
        user_id      BIGINT NOT NULL,
        key_hash     VARCHAR(128) NOT NULL,
        label        VARCHAR(255),
        active       BOOLEAN NOT NULL DEFAULT TRUE,
        expires_at   TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_used_at TIMESTAMPTZ,
        CONSTRAINT fk_api_key_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
        CONSTRAINT uq_api_key_hash UNIQUE (key_hash)
    );
    CREATE INDEX IF NOT EXISTS idx_apikey_user_id ON public.api_key(user_id);
EXCEPTION WHEN others THEN
    -- ignore if migrations framework handles creation differently
    NULL;
END $$;

