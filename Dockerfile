FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for psycopg2 and Node.js
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Install Node.js 18 LTS (needed for Prisma node script)
RUN mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_18.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update -y && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Copy Python deps first for caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Prisma deps
WORKDIR /app/database/prisma
COPY database/prisma/package.json database/prisma/package-lock.json ./
RUN npm ci

# Copy project
WORKDIR /app
COPY . .

# Expose API port
EXPOSE 8000

# Start the API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
