import re
import json
import requests
import gspread
from agents.tools.calc import calc_tool
from agents.tools.google import google_search_tool
from agents.tools.websearch import websearch_tool
from agents.tools.spreadsheet import spreadsheet_tool


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


def test_spreadsheet_tool(monkeypatch):
    calls = []

    class FakeWorksheet:
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
        def worksheet(self, name):
            return FakeWorksheet()

        def get_worksheet(self, index):
            return FakeWorksheet()

    class FakeClient:
        def open_by_key(self, key):
            return FakeSheet()

    def fake_service_account(filename):
        return FakeClient()

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "creds.json")
    monkeypatch.setattr(gspread, "service_account", fake_service_account)

    # read all values
    res = spreadsheet_tool.func(json.dumps({"action": "read", "spreadsheet_id": "id"}))
    assert res == json.dumps([["a", "b"]])

    # add row
    spreadsheet_tool.func(
        json.dumps({"action": "add", "spreadsheet_id": "id", "values": [1, 2]})
    )
    assert ("add", [1, 2]) in calls

    # update
    spreadsheet_tool.func(
        json.dumps({"action": "update", "spreadsheet_id": "id", "range": "A1", "values": 5})
    )
    assert ("update", "A1", 5) in calls

    # clear
    spreadsheet_tool.func(
        json.dumps({"action": "clear", "spreadsheet_id": "id", "range": "A1"})
    )
    assert ("clear", ["A1"]) in calls

    assert re.fullmatch(r"[A-Za-z0-9_-]+", spreadsheet_tool.name)
