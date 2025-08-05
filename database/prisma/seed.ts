// database/prisma/seed.ts
import { PrismaClient } from '@prisma/client'
const prisma = new PrismaClient()

async function main() {
  const user = await prisma.user.create({
    data: {
      email: "admin@example.com",
      name: "Admin",
      agents: {
        create: {
          name: "Agent CS",
          modelName: "gpt-4",
          systemMessage: "Kamu adalah CS ramah.",
          tools: ["google", "calc"],
        }
      }
    }
  })
  console.log("Seeded:", user)
}

main()
