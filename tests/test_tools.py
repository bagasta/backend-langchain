import re
import requests
from agents.tools.calc import calc_tool
from agents.tools.google import google_search_tool
from agents.tools.websearch import websearch_tool


def test_calc_tool():
    assert calc_tool.func("2 + 2") == "4"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", calc_tool.name)


def test_google_search_tool():
    result = google_search_tool.func("LangChain")
    assert "LangChain" in result
    assert re.fullmatch(r"[A-Za-z0-9_-]+", google_search_tool.name)


def test_websearch_tool(monkeypatch):
    def fake_post(url, headers, json, timeout):  # pragma: no cover - same for all
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "FooProduct"}}]}

        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(requests, "post", fake_post)

    result = websearch_tool.func("something")
    assert result == "FooProduct"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", websearch_tool.name)
