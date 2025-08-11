import json
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
    monkeypatch.setattr(
        "router.agents.get_auth_urls", lambda tools, state=None: {}
    )
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


def test_create_agent_returns_gmail_auth_url(monkeypatch, tmp_path):
    secrets = tmp_path / "client.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "cid"}}))
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "https://example.com/callback")

    monkeypatch.setattr(
        "router.agents.create_agent_record", lambda owner, name, config: "id1"
    )

    payload = {
        "owner_id": "user1",
        "name": "demo",
        "config": {
            "model_name": "gpt-4",
            "system_message": "hi",
            "tools": ["gmail_search"],
            "memory_enabled": False,
        },
    }
    response = client.post("/agents/", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == "id1"
    assert "auth_urls" in data
    assert data["auth_urls"]["gmail"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )


def test_run_agent_returns_400_on_iteration_limit(monkeypatch):
    def fake_get(agent_id: str) -> AgentConfig:
        return AgentConfig(
            model_name="gpt-4", system_message="hi", tools=[], memory_enabled=False
        )

    def fake_run(agent_id, config, message):
        assert agent_id == "demo"
        raise ValueError("Agent execution stopped before producing a final answer.")

    monkeypatch.setattr("router.agents.get_agent_config", fake_get)
    monkeypatch.setattr("router.agents.run_custom_agent", fake_run)

    response = client.post(
        "/agents/demo/run", json={"message": "hello", "openai_api_key": "key"}
    )
    assert response.status_code == 400
    assert "stopped before producing" in response.json()["detail"]
