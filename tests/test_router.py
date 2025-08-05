import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_run_agent_returns_400_when_api_key_missing():
    payload = {
        "config": {
            "model_name": "gpt-4",
            "system_message": "hi",
            "tools": [],
            "memory_enabled": False
        },
        "message": "hello"
    }
    response = client.post("/agents/demo/run", json=payload)
    assert response.status_code == 400
    assert "OpenAI API key" in response.json()["detail"]
