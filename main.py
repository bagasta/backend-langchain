# FastAPI entry point
# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

# Load environment early so routers and tools can read it during import
load_dotenv()

from router.agents import router as agents_router
from database.memory_bootstrap import ensure_memory_database
from router.oauth import router as oauth_router
from router.gmail_status import router as gmail_status_router
from router.api_keys import router as api_keys_router

app = FastAPI(title="LangChain Modular Backend")

# CORS configuration (for browser-based clients)
origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "*")
origins = (
    [o.strip() for o in origins_env.split(",") if o.strip()]
    if origins_env != "*"
    else ["*"]
)
allow_credentials = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"
# Credentials are not allowed with wildcard origins
if origins == ["*"] and allow_credentials:
    allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Ensure memory DB exists on startup (best-effort)
@app.on_event("startup")
async def _bootstrap_memory_db():
    try:
        ensure_memory_database()
    except Exception:
        # Do not block startup if remote admin perms are missing
        pass

# mount router /agents
app.include_router(agents_router, prefix="/agents", tags=["agents"])
app.include_router(oauth_router, tags=["oauth"])  # includes /oauth/gmail/callback
app.include_router(gmail_status_router, tags=["gmail"])  # /gmail/status, /gmail/dry_send
app.include_router(api_keys_router, tags=["api_keys"])  # /api_keys/generate

@app.get("/")
async def root():
    return {"message": "LangChain backend is up 🚀"}
