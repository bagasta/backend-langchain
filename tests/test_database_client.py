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

