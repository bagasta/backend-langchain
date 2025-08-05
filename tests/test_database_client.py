import subprocess

import pytest

from config.schema import AgentConfig
from database import client


def test_create_agent_record_propagates_node_errors(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="boom")

    monkeypatch.setattr(client.subprocess, "run", fake_run)

    cfg = AgentConfig(model_name="gpt-4o-mini", system_message="", tools=[])
    with pytest.raises(RuntimeError, match="boom"):
        client.create_agent_record("owner", "name", cfg)


def test_create_agent_record_generates_prisma_client(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "npx":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"id":"1"}', stderr="")

    monkeypatch.setattr(client.subprocess, "run", fake_run)

    cfg = AgentConfig(model_name="gpt-4o-mini", system_message="", tools=[])
    client.create_agent_record("owner", "name", cfg)

    assert calls[0][:4] == ["npx", "prisma", "migrate", "deploy"]
    assert calls[1][:3] == ["npx", "prisma", "generate"]
    assert calls[2][0] == "node"

