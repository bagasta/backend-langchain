import re
import json
import requests
import gspread
from agents.tools.calc import calc_tool
from agents.tools.google_search import google_search_tool
from agents.tools.gmail import (
    gmail_search_tool,
    gmail_send_message_tool,
    gmail_read_messages,
    build_gmail_oauth_url,
)
from agents.tools.websearch import websearch_tool
from agents.tools.spreadsheet import spreadsheet_tool
from agents.tools.registry import TOOL_REGISTRY


def test_calc_tool():
    assert calc_tool.func("2 + 2") == "4"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", calc_tool.name)


def test_google_search_tool_fallback():
    """Without API keys the Google search tool returns an error message."""
    output = (
        google_search_tool.run("LangChain")
        if hasattr(google_search_tool, "run")
        else google_search_tool.func("LangChain")
    )
    assert "Google Search tool unavailable" in output
    assert re.fullmatch(r"[A-Za-z0-9_-]+", google_search_tool.name)


def test_gmail_tools_fallback():
    search_out = (
        gmail_search_tool.run("test")
        if hasattr(gmail_search_tool, "run")
        else gmail_search_tool.func("test")
    )
    assert "Gmail tool unavailable" in search_out
    send_out = (
        gmail_send_message_tool.run(
            "hi", to="a", subject="b"
        )
        if hasattr(gmail_send_message_tool, "run")
        else gmail_send_message_tool.func(
            "hi", to="a", subject="b"
        )
    )
    assert "Gmail tool unavailable" in send_out
    assert re.fullmatch(r"[A-Za-z0-9_-]+", gmail_search_tool.name)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", gmail_send_message_tool.name)


def test_agent_specific_gmail_requires_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_AGENT_CREDENTIALS_DIR", str(tmp_path / "google"))
    monkeypatch.setattr("utils.google_oauth.get_agent_google_token", lambda agent_id: None)

    result = gmail_read_messages(agent_id="new-agent")
    assert "oauth" in result.lower() or "authorize" in result.lower()


def test_build_gmail_oauth_url(monkeypatch, tmp_path):
    secrets = tmp_path / "client.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "cid"}}))
    monkeypatch.setenv("GMAIL_CLIENT_SECRETS_PATH", str(secrets))
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "https://example.com/callback")
    url = build_gmail_oauth_url("state123")
    assert "state123" in url
    assert "client_id=cid" in url


def test_all_google_tools_registered():
    names = [
        "google_search",
        "google_serper",
        "google_trends",
        "google_places",
        "google_finance",
        "google_cloud_text_to_speech",
        "google_jobs",
        "google_scholar",
        "google_books",
        "google_lens",
        "gmail_search",
        "gmail_send_message",
    ]
    for name in names:
        assert name in TOOL_REGISTRY
        tool = TOOL_REGISTRY[name]
        assert re.fullmatch(r"[A-Za-z0-9_-]+", tool.name)


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


def test_spreadsheet_tool(monkeypatch):
    calls = []

    class FakeWorksheet:
        def __init__(self, title="Sheet1"):
            self.title = title

        def get_all_values(self):
            return [["a", "b"]]

        def get(self, rng):
            return [["x"]]

        def append_row(self, values):
            calls.append(("add", values))

        def update(self, rng, values):
            calls.append(("update", rng, values))

        def batch_clear(self, ranges):
            calls.append(("clear", ranges))

    class FakeSheet:
        def __init__(self):
            self._worksheets = [FakeWorksheet("Sheet1"), FakeWorksheet("Sheet2")]

        def worksheet(self, name):
            for ws in self._worksheets:
                if ws.title == name:
                    return ws
            raise KeyError(name)

        def get_worksheet(self, index):
            return self._worksheets[index]

        def worksheets(self):
            return self._worksheets

    class FakeHttpClient:
        def __init__(self):
            self.timeout = None

        def set_timeout(self, timeout):
            self.timeout = timeout

    class FakeClient:
        def __init__(self):
            self.http_client = FakeHttpClient()

        def open_by_key(self, key):
            return FakeSheet()

    def fake_service_account(filename):
        return FakeClient()

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "creds.json")
    monkeypatch.setenv("SPREADSHEET_ID", "id")
    monkeypatch.setattr(gspread, "service_account", fake_service_account)

    # read all values using default spreadsheet id and automatic first sheet
    res = spreadsheet_tool.func({"action": "read"})
    assert res == json.dumps([["a", "b"]])

    # add row
    spreadsheet_tool.func({"action": "add", "values": [1, 2], "worksheet": "sheet1"})
    assert ("add", [1, 2]) in calls

    # update
    spreadsheet_tool.func({"action": "update", "range": "A1", "values": 5})
    assert ("update", "A1", 5) in calls

    # clear
    spreadsheet_tool.func({"action": "clear", "range": "A1"})
    assert ("clear", ["A1"]) in calls

    assert re.fullmatch(r"[A-Za-z0-9_-]+", spreadsheet_tool.name)
