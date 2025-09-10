-- Create users table (if not exists)
CREATE TABLE IF NOT EXISTS "public"."users" (
  "id" BIGSERIAL PRIMARY KEY,
  "email" TEXT NOT NULL UNIQUE,
  "name" TEXT NOT NULL,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create agent table (if not exists)
CREATE TABLE IF NOT EXISTS "public"."agent" (
  "id" BIGSERIAL PRIMARY KEY,
  "user_id" BIGINT NOT NULL REFERENCES "public"."users"("id") ON DELETE CASCADE,
  "nama_model" TEXT NOT NULL,
  "system_message" TEXT NOT NULL,
  "tools" TEXT NOT NULL,
  "agent_type" TEXT NOT NULL,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "idx_agent_user_id" ON "public"."agent"("user_id");

-- Create list_account table (if not exists)
CREATE TABLE IF NOT EXISTS "public"."list_account" (
  "id" BIGSERIAL PRIMARY KEY,
  "user_id" BIGINT NOT NULL REFERENCES "public"."users"("id") ON DELETE CASCADE,
  "agent_id" BIGINT NOT NULL REFERENCES "public"."agent"("id") ON DELETE CASCADE,
  "email" TEXT NOT NULL,
  "servicesaccount" JSONB NOT NULL,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "uq_list_account_user_agent_email" UNIQUE ("user_id", "agent_id", "email")
);

CREATE INDEX IF NOT EXISTS "idx_list_account_user_id" ON "public"."list_account"("user_id");
CREATE INDEX IF NOT EXISTS "idx_list_account_agent_id" ON "public"."list_account"("agent_id");

-- updated_at trigger function
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW."updated_at" = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach triggers for updated_at auto-update
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'agent_set_updated_at'
  ) THEN
    CREATE TRIGGER agent_set_updated_at
    BEFORE UPDATE ON "public"."agent"
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'list_account_set_updated_at'
  ) THEN
    CREATE TRIGGER list_account_set_updated_at
    BEFORE UPDATE ON "public"."list_account"
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'users_set_updated_at'
  ) THEN
    CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON "public"."users"
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;
