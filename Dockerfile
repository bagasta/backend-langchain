FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UVICORN_WORKERS=1

WORKDIR /app

# System deps: curl for healthcheck, Node.js for Prisma helper
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
 && rm -rf /var/lib/apt/lists/*

# Install Node.js 18 LTS (needed for Prisma node script)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
 && apt-get update -y \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Copy Python deps first for better Docker layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Prisma deps (Node modules for database/prisma only)
WORKDIR /app/database/prisma
COPY database/prisma/package.json database/prisma/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy project files
WORKDIR /app
COPY . .

# Create a non-root user and prepare writable dirs (for OAuth tokens)
RUN useradd -m -u 10001 appuser \
 && mkdir -p /app/credential_folder \
 && chown -R appuser:appuser /app
USER appuser

# Expose API port
EXPOSE 8000

# Simple container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/ || exit 1

# Start the API (workers overridable via UVICORN_WORKERS)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}"]
