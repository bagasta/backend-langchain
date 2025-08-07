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

    class FakeClient:
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
