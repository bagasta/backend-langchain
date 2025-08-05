# FastAPI entry point
# main.py
from fastapi import FastAPI
from router.agents import router as agents_router

app = FastAPI(title="LangChain Modular Backend")

# mount router /agents
app.include_router(agents_router, prefix="/agents", tags=["agents"])

@app.get("/")
async def root():
    return {"message": "LangChain backend is up 🚀"}
