-- Add agent_name column to the new snake_case agent table
-- Safe to run multiple times due to IF NOT EXISTS
ALTER TABLE "public"."agent"
  ADD COLUMN IF NOT EXISTS "agent_name" TEXT;

-- Optional: backfill strategy (commented). Uncomment and adjust as desired.
-- UPDATE "public"."agent"
--   SET "agent_name" = CONCAT('agent-', id)
-- WHERE "agent_name" IS NULL;

