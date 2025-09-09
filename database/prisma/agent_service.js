const { PrismaClient } = require('@prisma/client');

const prisma = new PrismaClient();

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  const data = Buffer.concat(chunks).toString();
  return JSON.parse(data);
}

async function main() {
  const command = process.argv[2];
  const payload = await readStdin();

  if (command === 'create') {
    // Ensure the owner exists; create a placeholder user if missing
    await prisma.user.upsert({
      where: { id: payload.ownerId },
      update: {},
      create: {
        id: payload.ownerId,
        email: `${payload.ownerId}@example.com`,
        name: payload.ownerId,
      },
    });

    const agent = await prisma.agent.create({
      data: {
        ownerId: payload.ownerId,
        name: payload.name,
        modelName: payload.config.model_name,
        systemMessage: payload.config.system_message,
        tools: payload.config.tools,
        memoryEnabled: payload.config.memory_enabled ?? false,
        memoryBackend: payload.config.memory_backend,
        agentType: payload.config.agent_type,
        maxIterations: payload.config.max_iterations,
        maxExecutionTime: payload.config.max_execution_time,
      },
    });
    console.log(JSON.stringify(agent));
  } else if (command === 'get') {
    const agent = await prisma.agent.findUnique({
      where: { id: payload.agent_id },
    });
    console.log(JSON.stringify(agent));
  } else if (command === 'list') {
    const agents = await prisma.agent.findMany({});
    console.log(JSON.stringify(agents));
  }
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
