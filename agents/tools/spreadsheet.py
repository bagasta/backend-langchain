"""Google Spreadsheet tool for reading and modifying sheets."""

import json
import os
import re
import logging
from typing import Any, Dict, Union

import gspread
from langchain.agents import Tool

logger = logging.getLogger(__name__)


def _spreadsheet_action(input_data: Union[str, Dict[str, Any]]) -> str:
    """Perform operations on a Google Spreadsheet.

    Accepts a JSON string or dict with at least:
    - action: one of 'read', 'add', 'update', 'clear'
    - spreadsheet_id or spreadsheet_url: target sheet identifier
    - worksheet: worksheet title or index (optional)
    Additional fields depend on action:
      * read: optional 'range'
      * add: values -> list of values to append
      * update: range and values (list or list of lists)
      * clear: range to clear
    Authentication uses service account credentials pointed to by
    `GOOGLE_APPLICATION_CREDENTIALS` env var.
    """

    if isinstance(input_data, str):
        params: Dict[str, Any] = json.loads(input_data)
    elif isinstance(input_data, dict):
        params = input_data
    else:  # pragma: no cover - defensive
        raise TypeError("input must be a JSON string or dict")
    action = params.get("action")
    spreadsheet_id = params.get("spreadsheet_id")
    if not spreadsheet_id:
        url = params.get("spreadsheet_url")
        if url:
            match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
            if match:
                spreadsheet_id = match.group(1)
    if not spreadsheet_id:
        spreadsheet_id = os.getenv("SPREADSHEET_ID")
    worksheet_ref = params.get("worksheet")
    if not action or not spreadsheet_id:
        raise ValueError("'action' and 'spreadsheet_id' are required")

    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS not set for spreadsheet tool")

    logger.info("Spreadsheet action '%s' on %s", action, spreadsheet_id)
    client = gspread.service_account(filename=creds_path)
    timeout = float(os.getenv("SPREADSHEET_TIMEOUT", "30"))
    if hasattr(client, "http_client"):
        try:
            client.http_client.set_timeout(timeout)
        except Exception:  # pragma: no cover - best effort
            logger.warning("Could not set timeout on gspread client")

    sheet = client.open_by_key(spreadsheet_id)
    worksheets = {ws.title.lower(): ws for ws in sheet.worksheets()}
    if worksheet_ref is None:
        ws = sheet.get_worksheet(0)
    elif isinstance(worksheet_ref, int):
        ws = sheet.get_worksheet(worksheet_ref)
    else:
        ws = worksheets.get(str(worksheet_ref).lower())
        if ws is None:
            raise ValueError(f"worksheet '{worksheet_ref}' not found")

    if action == "read":
        rng = params.get("range")
        logger.info("Reading range '%s' from worksheet '%s'", rng or "all", ws.title)
        if rng:
            values = ws.get(rng)
        else:
            values = ws.get_all_values()
        return json.dumps(values)
    if action == "add":
        values = params.get("values")
        if not isinstance(values, list):
            raise ValueError("'values' must be provided as list for add action")
        logger.info("Appending row to '%s': %s", ws.title, values)
        ws.append_row(values)
        return "row added"
    if action == "update":
        rng = params.get("range")
        values = params.get("values")
        if not rng or values is None:
            raise ValueError("'range' and 'values' required for update action")
        logger.info("Updating range '%s' on '%s'", rng, ws.title)
        ws.update(rng, values)
        return "range updated"
    if action == "clear":
        rng = params.get("range")
        if not rng:
            raise ValueError("'range' required for clear action")
        logger.info("Clearing range '%s' on '%s'", rng, ws.title)
        ws.batch_clear([rng])
        return "range cleared"

    raise ValueError(f"unknown action '{action}'")


spreadsheet_tool = Tool(
    name="spreadsheet",
    func=_spreadsheet_action,
    description=(
        "Interact with Google Sheets. Input is JSON with fields: action"
        " ('read','add','update','clear'), spreadsheet_id or spreadsheet_url,"
        " worksheet (title or index; default first sheet), and action-specific params."),
)

__all__ = ["spreadsheet_tool"]
