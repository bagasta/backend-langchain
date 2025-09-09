-- AlterTable
ALTER TABLE "public"."Agent" ADD COLUMN     "agentType" TEXT NOT NULL DEFAULT 'chat-conversational-react-description';
ALTER TABLE "public"."Agent" ADD COLUMN     "maxIterations" INTEGER;
ALTER TABLE "public"."Agent" ADD COLUMN     "maxExecutionTime" DOUBLE PRECISION;
