import re
from agents.tools.calc import calc_tool
from agents.tools.google import google_search_tool


def test_calc_tool():
    assert calc_tool.func("2 + 2") == "4"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", calc_tool.name)


def test_google_search_tool():
    result = google_search_tool.func("LangChain")
    assert "LangChain" in result
    assert re.fullmatch(r"[A-Za-z0-9_-]+", google_search_tool.name)
