"""Google Spreadsheet tool for reading and modifying sheets."""

import json
import os
from typing import Any, Dict

import gspread
from langchain.agents import Tool


def _spreadsheet_action(input_str: str) -> str:
    """Perform operations on a Google Spreadsheet.

    Expected JSON input with at least:
    - action: one of 'read', 'add', 'update', 'clear'
    - spreadsheet_id: target sheet ID
    - worksheet: worksheet title or index (optional, default 'Sheet1')
    Additional fields depend on action:
      * read: optional 'range'
      * add: values -> list of values to append
      * update: range and values (list or list of lists)
      * clear: range to clear
    Authentication uses service account credentials pointed to by
    `GOOGLE_APPLICATION_CREDENTIALS` env var.
    """

    params: Dict[str, Any] = json.loads(input_str)
    action = params.get("action")
    spreadsheet_id = params.get("spreadsheet_id")
    worksheet_ref = params.get("worksheet", "Sheet1")
    if not action or not spreadsheet_id:
        raise ValueError("'action' and 'spreadsheet_id' are required")

    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS not set for spreadsheet tool")

    client = gspread.service_account(filename=creds_path)
    sheet = client.open_by_key(spreadsheet_id)
    # determine worksheet
    if isinstance(worksheet_ref, int):
        ws = sheet.get_worksheet(worksheet_ref)
    else:
        ws = sheet.worksheet(worksheet_ref)

    if action == "read":
        rng = params.get("range")
        if rng:
            values = ws.get(rng)
        else:
            values = ws.get_all_values()
        return json.dumps(values)
    if action == "add":
        values = params.get("values")
        if not isinstance(values, list):
            raise ValueError("'values' must be provided as list for add action")
        ws.append_row(values)
        return "row added"
    if action == "update":
        rng = params.get("range")
        values = params.get("values")
        if not rng or values is None:
            raise ValueError("'range' and 'values' required for update action")
        ws.update(rng, values)
        return "range updated"
    if action == "clear":
        rng = params.get("range")
        if not rng:
            raise ValueError("'range' required for clear action")
        ws.batch_clear([rng])
        return "range cleared"

    raise ValueError(f"unknown action '{action}'")


spreadsheet_tool = Tool(
    name="spreadsheet",
    func=_spreadsheet_action,
    description=(
        "Interact with Google Sheets. Input is JSON with fields: action"
        " ('read','add','update','clear'), spreadsheet_id, worksheet (title or index),"
        " and action-specific params."),
)

__all__ = ["spreadsheet_tool"]
