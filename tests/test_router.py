import pytest
import pytest
from fastapi.testclient import TestClient
from main import app
from config.schema import AgentConfig

client = TestClient(app)


def test_run_agent_returns_400_when_api_key_missing(monkeypatch):
    def fake_get(agent_id: str) -> AgentConfig:
        return AgentConfig(
            model_name="gpt-4", system_message="hi", tools=[], memory_enabled=False
        )

    monkeypatch.setattr("router.agents.get_agent_config", fake_get)
    response = client.post("/agents/demo/run", json={"message": "hello"})
    assert response.status_code == 400
    assert "OpenAI API key" in response.json()["detail"]


def test_create_agent_persists(monkeypatch):
    def fake_create(owner_id, name, config):
        assert owner_id == "user1"
        assert name == "demo"
        assert isinstance(config, AgentConfig)
        return "new-id"

    monkeypatch.setattr("router.agents.create_agent_record", fake_create)
    payload = {
        "owner_id": "user1",
        "name": "demo",
        "config": {
            "model_name": "gpt-4",
            "system_message": "hi",
            "tools": [],
            "memory_enabled": False,
        },
    }
    response = client.post("/agents/", json=payload)
    assert response.status_code == 200
    assert response.json()["agent_id"] == "new-id"
