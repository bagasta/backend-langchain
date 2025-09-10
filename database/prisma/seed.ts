// database/prisma/seed.ts
import { PrismaClient } from '@prisma/client'
const prisma = new PrismaClient()

async function main() {
  const user = await prisma.user.create({
    data: {
      email: "admin@example.com",
      name: "Admin",
    }
  })

  const agent = await prisma.agent.create({
    data: {
      user_id: user.id,
      nama_model: "gpt-4",
      system_message: "Kamu adalah CS ramah.",
      tools: JSON.stringify(["google", "calc"]),
      agent_type: "chat-conversational-react-description",
    }
  })
  console.log("Seeded:", { user, agent })
}

main()
