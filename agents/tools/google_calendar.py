"""
Google Calendar Tools for LangChain Agent
This module provides tools for interacting with Google Calendar API.

Includes a server-friendly OAuth URL builder for initiating authorization
flows (similar to the Gmail helper), so frontends can link users to
grant Calendar access.
"""

import os
import datetime
import logging
import re
from typing import Dict, List, Optional, Any, Union, Type, Tuple
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from langchain.tools import BaseTool
# Prefer native Pydantic v2; fall back to v1 compatibility if needed
try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field  # type: ignore

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request, AuthorizedSession
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except Exception:  # pragma: no cover - optional dependency
    Credentials = None  # type: ignore
    Request = None  # type: ignore
    AuthorizedSession = None  # type: ignore
    InstalledAppFlow = None  # type: ignore
    build = None  # type: ignore
    HttpError = Exception  # type: ignore

from utils.google_oauth import ensure_agent_token_file

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Calendar API scopes (overridable via env)
SCOPES = [
    s.strip()
    for s in os.getenv(
        "GCAL_SCOPES",
        os.getenv("CALENDAR_SCOPES", "https://www.googleapis.com/auth/calendar"),
    ).split(",")
    if s.strip()
]


def _default_calendar_dir() -> str:
    base_dir = os.getenv("GCAL_CREDENTIALS_DIR") or os.getenv("CREDENTIALS_DIR")
    if base_dir:
        if base_dir == os.getenv("CREDENTIALS_DIR"):
            return os.path.join(base_dir, "calendar")
        return base_dir
    return os.path.join(os.getcwd(), ".credentials", "calendar")


@dataclass
class CalendarConfig:
    """Configuration for Google Calendar tools"""
    credentials_file: str = "credentials.json"
    # Leave blank by default so we can derive a provider-specific token path
    # (avoids colliding with Gmail's token.json and causing invalid scopes)
    token_file: str = ""
    timezone: str = "Asia/Jakarta"
    max_results: int = 10
    default_reminder_minutes: int = 10
    agent_id: Optional[str] = None


class GoogleCalendarTools:
    """Wrapper class for Google Calendar tools"""
    
    def __init__(self, config: Optional[CalendarConfig] = None):
        """Initialize Google Calendar tools
        
        Args:
            config: CalendarConfig object with settings
        """
        self.config = config or CalendarConfig()
        # Normalize paths: allow directories via env to simplify setup
        try:
            if os.path.isdir(self.config.credentials_file):
                self.config.credentials_file = os.path.join(self.config.credentials_file, "credentials.json")
        except Exception:
            pass
        try:
            # Default token next to credentials when not set explicitly (provider-specific)
            if not self.config.token_file:
                base_dir = os.path.dirname(self.config.credentials_file) if self.config.credentials_file else _default_calendar_dir()
                if not base_dir:
                    base_dir = _default_calendar_dir()
                self.config.token_file = os.path.join(base_dir, "calendar_token.json")
            elif os.path.isdir(self.config.token_file):
                self.config.token_file = os.path.join(self.config.token_file, "calendar_token.json")
        except Exception:
            pass
        self.service = None
        self.session: Optional[AuthorizedSession] = None
        self._init_error: Optional[str] = None
        try:
            self._initialize_service()
        except Exception as exc:
            # Allow the agent to load even when credentials are missing so we can surface a friendly error at call time.
            self._init_error = str(exc)
            logger.info("Google Calendar client unavailable until OAuth completes: %s", exc)
    
    def _initialize_service(self):
        """Initialize Google Calendar service"""
        try:
            creds = self._get_credentials()
            fallback_reason: Optional[str] = None
            if build is not None:
                try:
                    # Prefer discovery client when available
                    self.service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
                    logger.info("Google Calendar service initialized successfully")
                except Exception as build_exc:
                    fallback_reason = str(build_exc)
                    self.service = None
            else:
                fallback_reason = "googleapiclient.discovery not available"

            if self.service:
                return

            if AuthorizedSession is None:
                raise RuntimeError(
                    "Google Calendar discovery client unavailable and REST session cannot be created without google-auth libraries."
                )

            if fallback_reason:
                logger.info(
                    "Calendar discovery client unavailable (%s); falling back to REST session", fallback_reason
                )
            self.session = AuthorizedSession(creds)
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar service: {e}")
            raise

    def _require_client(self) -> None:
        """Ensure a usable client/session exists before making API calls."""

        if self._init_error:
            raise RuntimeError("Google Calendar tool unavailable: " + self._init_error)
        if not (self.service or self.session):
            raise RuntimeError("Google Calendar client not initialized")

    # -----------------------------
    # Helpers
    # -----------------------------
    @staticmethod
    def _parse_dt_local(dt_str: str, tz_name: str) -> datetime.datetime:
        """Parse an ISO-ish datetime string and localize to tz.

        - Accepts strings with or without timezone. Supports trailing 'Z'.
        - If input has tzinfo, convert to target timezone.
        - If input is naive, assume it is already in target timezone.
        """
        if not dt_str:
            raise ValueError("Datetime string is required")
        # Normalize Zulu suffix to +00:00 for fromisoformat
        cleaned = dt_str.strip().replace("Z", "+00:00")
        tz = ZoneInfo(tz_name)
        try:
            dt = datetime.datetime.fromisoformat(cleaned)
        except Exception as e:
            raise ValueError(f"Invalid datetime format: '{dt_str}'. Use YYYY-MM-DDTHH:MM:SS[Z|±HH:MM]") from e
        if dt.tzinfo is None:
            # Treat as local time in requested timezone
            return dt.replace(tzinfo=tz)
        # Convert to requested timezone for consistency
        return dt.astimezone(tz)

    @staticmethod
    def _normalize_attendees(attendees: Optional[List[str]]) -> Tuple[List[Dict[str, str]], List[str]]:
        """Normalize attendee emails.

        - Adds default domain if missing (env GCAL_DEFAULT_EMAIL_DOMAIN or gmail.com).
        - Filters out obviously invalid values.
        Returns (normalized_list, rejected_inputs).
        """
        if not attendees:
            return [], []
        default_domain = os.getenv("GCAL_DEFAULT_EMAIL_DOMAIN") or os.getenv("DEFAULT_EMAIL_DOMAIN") or "gmail.com"
        normalized: List[Dict[str, str]] = []
        rejected: List[str] = []
        for raw in attendees:
            if not raw:
                continue
            s = str(raw).strip()
            if "@" not in s:
                s = f"{s}@{default_domain}"
            # Basic sanity check (avoid spaces and ensure domain contains a dot)
            if " " in s or s.count("@") != 1 or "." not in s.split("@", 1)[1]:
                rejected.append(raw)
                continue
            normalized.append({"email": s})
        return normalized, rejected

    @staticmethod
    def _raise_for_status_with_detail(resp, context: str = "") -> None:
        """Raise a detailed error including API response body when available."""
        try:
            resp.raise_for_status()
        except Exception as e:
            detail = ""
            try:
                j = resp.json()
                # Common Google error envelope
                if isinstance(j, dict):
                    err = j.get("error")
                    if isinstance(err, dict):
                        detail = err.get("message") or str(err)
                    else:
                        detail = str(j)
                else:
                    detail = str(j)
            except Exception:
                # Fallback to raw text (trim to avoid noisy output)
                try:
                    detail = (resp.text or "").strip()
                except Exception:
                    detail = ""
            msg = f"Calendar API {context} failed: {resp.status_code} {getattr(resp, 'reason', '')}. {detail}".strip()
            raise RuntimeError(msg) from e

    # -----------------------------
    # REST fallback helpers
    # -----------------------------
    @property
    def _base_url(self) -> str:
        return "https://www.googleapis.com/calendar/v3"

    def events_list(self, **params) -> Dict[str, Any]:
        self._require_client()
        if self.service:
            return self.service.events().list(**params).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        calendar_id = params.pop('calendarId', 'primary')
        resp = self.session.get(f"{self._base_url}/calendars/{calendar_id}/events", params=params, timeout=20)
        self._raise_for_status_with_detail(resp, context="events.list")
        return resp.json()

    def events_get(self, calendarId: str, eventId: str) -> Dict[str, Any]:
        self._require_client()
        if self.service:
            return self.service.events().get(calendarId=calendarId, eventId=eventId).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.get(f"{self._base_url}/calendars/{calendarId}/events/{eventId}", timeout=20)
        self._raise_for_status_with_detail(resp, context="events.get")
        return resp.json()

    def events_insert(self, calendarId: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self._require_client()
        if self.service:
            return self.service.events().insert(calendarId=calendarId, body=body).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.post(f"{self._base_url}/calendars/{calendarId}/events", json=body, timeout=20)
        self._raise_for_status_with_detail(resp, context="events.insert")
        return resp.json()

    def events_update(self, calendarId: str, eventId: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self._require_client()
        if self.service:
            try:
                logging.debug(f"Using service library to update event {eventId} with body: {body}")
                result = self.service.events().update(calendarId=calendarId, eventId=eventId, body=body).execute()
                logging.debug(f"Service library update result: {result}")
                return result
            except Exception as service_error:
                logging.error(f"Service library update error: {service_error}")
                raise service_error
        if not self.session:
            raise RuntimeError("Calendar client not initialized")

        try:
            logging.debug(f"Using REST API to update event {eventId} with body: {body}")
            resp = self.session.put(f"{self._base_url}/calendars/{calendarId}/events/{eventId}", json=body, timeout=20)
            logging.debug(f"REST API response status: {resp.status_code}")
            logging.debug(f"REST API response body: {resp.text}")
            self._raise_for_status_with_detail(resp, context="events.update")
            return resp.json()
        except Exception as rest_error:
            logging.error(f"REST API update error: {rest_error}")
            raise rest_error

    def events_delete(self, calendarId: str, eventId: str) -> None:
        self._require_client()
        if self.service:
            self.service.events().delete(calendarId=calendarId, eventId=eventId).execute()
            return
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.delete(f"{self._base_url}/calendars/{calendarId}/events/{eventId}", timeout=20)
        # Google returns 204 No Content on success
        if not (200 <= resp.status_code < 300):
            self._raise_for_status_with_detail(resp, context="events.delete")

    def freebusy_query(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self._require_client()
        if self.service:
            return self.service.freebusy().query(body=body).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.post(f"{self._base_url}/freeBusy", json=body, timeout=20)
        self._raise_for_status_with_detail(resp, context="freebusy.query")
        return resp.json()

    def calendar_list_list(self, **params) -> Dict[str, Any]:
        self._require_client()
        if self.service:
            return self.service.calendarList().list(**params).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.get(f"{self._base_url}/users/me/calendarList", params=params, timeout=20)
        self._raise_for_status_with_detail(resp, context="calendarList.list")
        return resp.json()
    
    def _get_credentials(self) -> Credentials:
        """Get or refresh Google Calendar credentials

        Returns:
            Credentials object for Google Calendar API
        """
        if Credentials is None or Request is None:
            raise RuntimeError("Google client libraries not installed for Calendar operations.")
        creds = None

        fallback_token = self.config.token_file or os.path.join(_default_calendar_dir(), "calendar_token.json")
        token_path, _ = ensure_agent_token_file(self.config.agent_id, fallback_token, filename="token.json")
        token_file = token_path or fallback_token
        self.config.token_file = token_file

        # Load existing token
        if token_file and os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            # If token exists but scopes are insufficient, force re-auth (don't attempt to use Gmail token)
            try:
                have_scopes = set(creds.scopes or [])
                need_scopes = set(SCOPES)
                if need_scopes and not need_scopes.issubset(have_scopes):
                    raise ValueError(
                        f"Existing token has wrong scopes. Have: {sorted(have_scopes)}, need: {sorted(need_scopes)}"
                    )
            except Exception as scope_exc:
                # Surface a clear error so the caller can prompt re-auth via OAuth URL
                raise RuntimeError(
                    f"Google Calendar token at {token_file} is not authorized for required scopes. "
                    f"{scope_exc}. Please re-authorize via the Calendar OAuth link."
                )

        # Refresh or create new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                # Try to refresh silently when we can
                creds.refresh(Request())
            else:
                # In server context, do not start a local OAuth server which can hang the process.
                # Instead, instruct the caller to use the explicit OAuth link (exposed via /agents create auth_urls).
                if not os.path.exists(self.config.credentials_file):
                    raise FileNotFoundError(
                        f"Credentials file not found: {self.config.credentials_file}"
                    )
                who = f" for agent {self.config.agent_id}" if self.config.agent_id else ""
                raise RuntimeError(
                    "Google Calendar OAuth token not found or invalid"
                    + who
                    + f" at {token_file}. Please authorize via the Calendar OAuth URL and retry."
                )

        return creds
    
    def get_langchain_tools(self) -> List[BaseTool]:
        """Get list of LangChain tools for Google Calendar

        Returns:
            List of LangChain tools
        """
        tools = [
            CreateEventTool(calendar_tools=self),
            ListEventsTool(calendar_tools=self),
            GetEventTool(calendar_tools=self),
            UpdateEventTool(calendar_tools=self),
            DeleteEventTool(calendar_tools=self),
            SearchEventsTool(calendar_tools=self),
            GetFreeBusyTool(calendar_tools=self),
            ListCalendarsTool(calendar_tools=self),
            CalendarUnifiedTool(calendar_tools=self),
        ]

        return tools


# -----------------------------
# OAuth URL helper (for your UI)
# -----------------------------
def build_calendar_oauth_url(state: Optional[str] = None) -> Optional[str]:
    """
    Return an OAuth login URL for Google Calendar if client_id + redirect_uri are available.

    Reads:
      - GCAL_CLIENT_SECRETS_PATH or GCAL_CREDENTIALS_PATH (or default under .credentials/calendar/credentials.json)
      - GCAL_REDIRECT_URI (callback URL, e.g., http://localhost:8000/oauth/calendar/callback)
      - GCAL_SCOPES or CALENDAR_SCOPES (comma-separated)
    """
    # Resolve secrets path
    secrets_candidates = [
        os.getenv("GCAL_CLIENT_SECRETS_PATH"),
        # If GCAL_CREDENTIALS_PATH is a dir, append credentials.json
        (os.path.join(os.getenv("GCAL_CREDENTIALS_PATH", ""), "credentials.json")
         if os.getenv("GCAL_CREDENTIALS_PATH") and os.path.isdir(os.getenv("GCAL_CREDENTIALS_PATH", ""))
         else os.getenv("GCAL_CREDENTIALS_PATH")),
        os.getenv("GOOGLE_CREDENTIALS_PATH"),
        os.path.join(os.getcwd(), "credential_folder", "credentials.json"),
        os.path.join(_default_calendar_dir(), "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
    ]
    secrets_path = next((p for p in secrets_candidates if p and os.path.exists(p)), None)
    # Prefer a universal redirect if configured, else provider-specific
    redirect_uri = (
        os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        or os.getenv("OAUTH_REDIRECT_URI")
        or os.getenv("GCAL_REDIRECT_URI")
        or os.getenv("CALENDAR_REDIRECT_URI")
    )
    scopes = [
        s.strip()
        for s in os.getenv(
            "GCAL_SCOPES", os.getenv("CALENDAR_SCOPES", "https://www.googleapis.com/auth/calendar")
        ).split(",")
        if s.strip()
    ]
    if not secrets_path or not redirect_uri or not os.path.exists(secrets_path):
        return None

    try:
        import json as _json
        with open(secrets_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        client_id = (
            data.get("web", {}) or {}
        ).get("client_id") or (data.get("installed", {}) or {}).get("client_id")
        if not client_id:
            return None
    except Exception:
        return None

    from urllib.parse import urlencode
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


# Pydantic models for tool inputs
class CreateEventInput(BaseModel):
    """Input schema for creating calendar event"""
    summary: str = Field(description="Event title/summary")
    start_time: str = Field(description="Start time in ISO format (YYYY-MM-DDTHH:MM:SS)")
    end_time: str = Field(description="End time in ISO format (YYYY-MM-DDTHH:MM:SS)")
    description: Optional[str] = Field(None, description="Event description")
    location: Optional[str] = Field(None, description="Event location")
    attendees: Optional[List[str]] = Field(None, description="List of attendee emails")
    reminder_minutes: Optional[int] = Field(10, description="Reminder in minutes before event")


class ListEventsInput(BaseModel):
    """Input schema for listing calendar events"""
    max_results: Optional[int] = Field(10, description="Maximum number of events to return")
    time_min: Optional[str] = Field(None, description="Start time for events in ISO format")
    time_max: Optional[str] = Field(None, description="End time for events in ISO format")
    query: Optional[str] = Field(None, description="Search query for events")


class GetEventInput(BaseModel):
    """Input schema for getting a specific event"""
    event_id: str = Field(description="Event ID to retrieve")


class UpdateEventInput(BaseModel):
    """Input schema for updating calendar event"""
    event_id: str = Field(description="Event ID to update")
    summary: Optional[str] = Field(None, description="New event title/summary")
    start_time: Optional[str] = Field(None, description="New start time in ISO format")
    end_time: Optional[str] = Field(None, description="New end time in ISO format")
    description: Optional[str] = Field(None, description="New event description")
    location: Optional[str] = Field(None, description="New event location")


class DeleteEventInput(BaseModel):
    """Input schema for deleting calendar event"""
    event_id: str = Field(description="Event ID to delete")


class SearchEventsInput(BaseModel):
    """Input schema for searching calendar events"""
    query: str = Field(description="Search query")
    max_results: Optional[int] = Field(10, description="Maximum number of results")
    time_min: Optional[str] = Field(None, description="Start time for search in ISO format")
    time_max: Optional[str] = Field(None, description="End time for search in ISO format")


class GetFreeBusyInput(BaseModel):
    """Input schema for checking free/busy times"""
    time_min: str = Field(description="Start time for checking in ISO format")
    time_max: str = Field(description="End time for checking in ISO format")
    calendars: Optional[List[str]] = Field(None, description="List of calendar IDs to check")


class CalendarUnifiedInput(BaseModel):
    """Unified Calendar tool input schema"""
    action: str = Field(description="Action to perform: create | list | get | update | delete | search | freebusy | list_calendars")
    # create
    summary: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: Optional[List[str]] = None
    reminder_minutes: Optional[int] = 10
    # list/search
    max_results: Optional[int] = 10
    time_min: Optional[str] = None
    time_max: Optional[str] = None
    query: Optional[str] = None
    # get/update/delete
    event_id: Optional[str] = None
    # freebusy
    calendars: Optional[List[str]] = None


# LangChain Tool Classes
class CreateEventTool(BaseTool):
    """Tool for creating Google Calendar events"""
    name: str = "create_calendar_event"
    description: str = "Create a new event in Google Calendar"
    args_schema: Type[BaseModel] = CreateEventInput
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        reminder_minutes: Optional[int] = 10
    ) -> str:
        """Create a new calendar event"""
        try:
            # Parse times with robust handling and timezone
            tz_name = self.calendar_tools.config.timezone
            start_dt = self.calendar_tools._parse_dt_local(start_time, tz_name)
            end_dt = self.calendar_tools._parse_dt_local(end_time, tz_name)
            if end_dt <= start_dt:
                return "Error creating event: end_time must be after start_time"

            # Build event body
            event = {
                'summary': summary,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': tz_name,
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': tz_name,
                }
            }

            if description:
                event['description'] = description
            if location:
                event['location'] = location
            normalized_attendees: List[Dict[str, str]] = []
            rejected_attendees: List[str] = []
            if attendees:
                normalized_attendees, rejected_attendees = self.calendar_tools._normalize_attendees(attendees)
                if normalized_attendees:
                    event['attendees'] = normalized_attendees
            if reminder_minutes:
                event['reminders'] = {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': int(reminder_minutes)},
                    ],
                }

            # Attempt creation
            try:
                result = self.calendar_tools.events_insert(
                    calendarId='primary',
                    body=event,
                )
            except Exception as e_first:
                # If attendees were present and may be invalid, retry once without them
                if 'attendees' in event and event['attendees']:
                    event.pop('attendees', None)
                    try:
                        result = self.calendar_tools.events_insert(
                            calendarId='primary',
                            body=event,
                        )
                        note = " (created without attendees due to invalid addresses)"
                        if rejected_attendees:
                            bad = ", ".join(map(str, rejected_attendees))
                            note = f" (invalid attendee(s): {bad}; created without attendees)"
                        return f"Event created successfully{note}: {result.get('htmlLink')}"
                    except Exception as e_second:
                        return f"Error creating event: {str(e_second)}"
                return f"Error creating event: {str(e_first)}"

            # Success on first try
            extra = ""
            if rejected_attendees:
                extra = f" (ignored invalid attendee(s): {', '.join(map(str, rejected_attendees))})"
            return f"Event created successfully{extra}: {result.get('htmlLink')}"

        except Exception as e:
            return f"Error creating event: {str(e)}"


class ListEventsTool(BaseTool):
    """Tool for listing Google Calendar events"""
    name: str = "list_calendar_events"
    description: str = "List upcoming events from Google Calendar"
    args_schema: Type[BaseModel] = ListEventsInput
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(
        self,
        max_results: Optional[int] = 10,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        query: Optional[str] = None
    ) -> str:
        """List calendar events"""
        try:
            # Set default time_min to now if not specified
            if not time_min:
                time_min = datetime.datetime.now().isoformat() + 'Z'
            else:
                tz = ZoneInfo(self.calendar_tools.config.timezone)
                time_min = datetime.datetime.fromisoformat(time_min).replace(tzinfo=tz).isoformat()
            
            # Build request parameters
            params = {
                'calendarId': 'primary',
                'timeMin': time_min,
                'maxResults': max_results,
                'singleEvents': True,
                'orderBy': 'startTime'
            }
            
            if time_max:
                tz = ZoneInfo(self.calendar_tools.config.timezone)
                params['timeMax'] = datetime.datetime.fromisoformat(time_max).replace(tzinfo=tz).isoformat()
            
            if query:
                params['q'] = query
            
            # Get events
            events_result = self.calendar_tools.events_list(**params)
            events = events_result.get('items', [])
            
            if not events:
                return "No upcoming events found."
            
            # Format events for output
            output = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                
                event_info = f"- {event['summary']}"
                event_info += f"\n  Start: {start}"
                event_info += f"\n  End: {end}"
                
                if 'location' in event:
                    event_info += f"\n  Location: {event['location']}"
                if 'description' in event:
                    event_info += f"\n  Description: {event['description'][:100]}..."
                
                event_info += f"\n  ID: {event['id']}"
                output.append(event_info)
            
            return "Upcoming events:\n" + "\n".join(output)
            
        except Exception as e:
            return f"Error listing events: {str(e)}"


class GetEventTool(BaseTool):
    """Tool for getting a specific Google Calendar event"""
    name: str = "get_calendar_event"
    description: str = "Get details of a specific calendar event by ID"
    args_schema: Type[BaseModel] = GetEventInput
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(self, event_id: str) -> str:
        """Get specific calendar event"""
        try:
            # Validate event_id format
            if not event_id or not isinstance(event_id, str):
                return "Error getting event: Invalid event ID format"

            # Clean event_id (remove any extra whitespace)
            event_id = event_id.strip()

            # Check if event_id looks like a valid Google Calendar event ID
            if not re.match(r'^[a-zA-Z0-9_\-]+$', event_id):
                return f"Error getting event: Invalid event ID format '{event_id}'. Event IDs should be alphanumeric strings."

            event = self.calendar_tools.events_get(
                calendarId='primary',
                eventId=event_id,
            )

            # Format event details
            output = f"Event Details:\n"
            output += f"Title: {event.get('summary', 'No title')}\n"

            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            output += f"Start: {start}\n"
            output += f"End: {end}\n"

            if 'location' in event:
                output += f"Location: {event['location']}\n"
            if 'description' in event:
                output += f"Description: {event['description']}\n"
            if 'attendees' in event:
                attendees = [a['email'] for a in event['attendees']]
                output += f"Attendees: {', '.join(attendees)}\n"

            output += f"Link: {event.get('htmlLink', 'N/A')}\n"
            output += f"Status: {event.get('status', 'N/A')}"

            return output

        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "Not Found" in error_msg:
                return f"Error getting event: Event with ID '{event_id}' not found. Please check:\n1. The event ID is correct\n2. The event exists in your primary calendar\n3. You have permission to access this event"
            else:
                return f"Error getting event: {error_msg}"


class UpdateEventTool(BaseTool):
    """Tool for updating Google Calendar events"""
    name: str = "update_calendar_event"
    description: str = "Update an existing calendar event"
    args_schema: Type[BaseModel] = UpdateEventInput
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(
        self,
        event_id: str,
        summary: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> str:
        """Update calendar event"""
        try:
            # Validate event_id format
            if not event_id or not isinstance(event_id, str):
                return "Error updating event: Invalid event ID format"

            # Clean event_id (remove any extra whitespace)
            event_id = event_id.strip()

            # Check if event_id looks like a valid Google Calendar event ID
            # Google Calendar event IDs are typically alphanumeric strings, sometimes with underscores
            if not re.match(r'^[a-zA-Z0-9_\-]+$', event_id):
                return f"Error updating event: Invalid event ID format '{event_id}'. Event IDs should be alphanumeric strings."

            # Get existing event
            try:
                event = self.calendar_tools.events_get(
                    calendarId='primary',
                    eventId=event_id,
                )

                # Log the original event for debugging
                logging.debug(f"Original event data: {event}")
            except Exception as get_error:
                error_msg = str(get_error)
                if "404" in error_msg or "Not Found" in error_msg:
                    return f"Error updating event: Event with ID '{event_id}' not found. Please check:\n1. The event ID is correct\n2. The event exists in your primary calendar\n3. You have permission to access this event"
                else:
                    return f"Error accessing event: {error_msg}"

            # Build update body with only the fields that need to be updated
            update_body = {}

            # Track which fields are being updated
            updated_fields = []

            # Update fields if provided
            if summary is not None:
                update_body['summary'] = summary
                updated_fields.append(f"summary='{summary}'")
                logging.debug(f"Updating summary to: {summary}")

            if description is not None:
                update_body['description'] = description
                updated_fields.append(f"description='{description}'")
                logging.debug(f"Updating description to: {description}")

            if location is not None:
                update_body['location'] = location
                updated_fields.append(f"location='{location}'")
                logging.debug(f"Updating location to: {location}")

            # Handle start time updates
            if start_time:
                try:
                    tz = ZoneInfo(self.calendar_tools.config.timezone)
                    start_dt = datetime.datetime.fromisoformat(start_time).replace(tzinfo=tz)
                    update_body['start'] = {
                        'dateTime': start_dt.isoformat(),
                        'timeZone': self.calendar_tools.config.timezone,
                    }
                    updated_fields.append(f"start_time={start_time}")
                    logging.debug(f"Updating start time to: {update_body['start']}")
                except ValueError as ve:
                    return f"Error updating event: Invalid start_time format '{start_time}'. Use ISO format like '2024-01-01T10:00:00'"

            # Handle end time updates
            if end_time:
                try:
                    tz = ZoneInfo(self.calendar_tools.config.timezone)
                    end_dt = datetime.datetime.fromisoformat(end_time).replace(tzinfo=tz)
                    update_body['end'] = {
                        'dateTime': end_dt.isoformat(),
                        'timeZone': self.calendar_tools.config.timezone,
                    }
                    updated_fields.append(f"end_time={end_time}")
                    logging.debug(f"Updating end time to: {update_body['end']}")
                except ValueError as ve:
                    return f"Error updating event: Invalid end_time format '{end_time}'. Use ISO format like '2024-01-01T11:00:00'"

            # If no fields were provided to update, return early
            if not updated_fields:
                return "No update fields provided. Please specify at least one field to update (summary, description, location, start_time, end_time)."

            # For Google Calendar API, we need to ensure we have at least one field to update
            # and we shouldn't include fields that aren't being updated
            logging.debug(f"Fields being updated: {', '.join(updated_fields)}")

            # Log the update body for debugging
            logging.debug(f"Update body being sent: {update_body}")

            # Update event
            try:
                updated_event = self.calendar_tools.events_update(
                    calendarId='primary',
                    eventId=event_id,
                    body=update_body,
                )

                # Log the response for debugging
                logging.debug(f"Update response: {updated_event}")

                return f"Event updated successfully: {updated_event.get('htmlLink')}"
            except Exception as update_error:
                error_msg = str(update_error)
                if "404" in error_msg or "Not Found" in error_msg:
                    return f"Error updating event: Event with ID '{event_id}' not found during update. The event may have been deleted or you may not have permission to modify it."
                else:
                    return f"Error updating event: {error_msg}"

        except Exception as e:
            return f"Error updating event: {str(e)}"


class DeleteEventTool(BaseTool):
    """Tool for deleting Google Calendar events"""
    name: str = "delete_calendar_event"
    description: str = "Delete a calendar event"
    args_schema: Type[BaseModel] = DeleteEventInput
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(self, event_id: str) -> str:
        """Delete calendar event"""
        try:
            # Validate event_id format
            if not event_id or not isinstance(event_id, str):
                return "Error deleting event: Invalid event ID format"

            # Clean event_id (remove any extra whitespace)
            event_id = event_id.strip()

            # Check if event_id looks like a valid Google Calendar event ID
            if not re.match(r'^[a-zA-Z0-9_\-]+$', event_id):
                return f"Error deleting event: Invalid event ID format '{event_id}'. Event IDs should be alphanumeric strings."

            self.calendar_tools.events_delete(
                calendarId='primary',
                eventId=event_id,
            )

            return f"Event {event_id} deleted successfully"

        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "Not Found" in error_msg:
                return f"Error deleting event: Event with ID '{event_id}' not found. Please check:\n1. The event ID is correct\n2. The event exists in your primary calendar\n3. You have permission to access this event"
            else:
                return f"Error deleting event: {error_msg}"


class SearchEventsTool(BaseTool):
    """Tool for searching Google Calendar events"""
    name: str = "search_calendar_events"
    description: str = "Search for events in Google Calendar"
    args_schema: Type[BaseModel] = SearchEventsInput
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(
        self,
        query: str,
        max_results: Optional[int] = 10,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None
    ) -> str:
        """Search calendar events"""
        try:
            # Set time range
            if not time_min:
                # Search from 1 year ago by default
                time_min = (datetime.datetime.now() - datetime.timedelta(days=365)).isoformat() + 'Z'
            else:
                tz = ZoneInfo(self.calendar_tools.config.timezone)
                time_min = datetime.datetime.fromisoformat(time_min).replace(tzinfo=tz).isoformat()
            
            params = {
                'calendarId': 'primary',
                'timeMin': time_min,
                'maxResults': max_results,
                'singleEvents': True,
                'orderBy': 'startTime',
                'q': query
            }
            
            if time_max:
                tz = ZoneInfo(self.calendar_tools.config.timezone)
                params['timeMax'] = datetime.datetime.fromisoformat(time_max).replace(tzinfo=tz).isoformat()
            
            # Search events
            events_result = self.calendar_tools.events_list(**params)
            events = events_result.get('items', [])
            
            if not events:
                return f"No events found matching '{query}'"
            
            # Format results
            output = [f"Found {len(events)} event(s) matching '{query}':\n"]
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                output.append(f"- {event['summary']} (Start: {start}, ID: {event['id']})")
            
            return "\n".join(output)
            
        except Exception as e:
            return f"Error searching events: {str(e)}"


class GetFreeBusyTool(BaseTool):
    """Tool for checking free/busy times"""
    name: str = "get_free_busy"
    description: str = "Check free/busy times for calendars"
    args_schema: Type[BaseModel] = GetFreeBusyInput
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(
        self,
        time_min: str,
        time_max: str,
        calendars: Optional[List[str]] = None
    ) -> str:
        """Get free/busy information"""
        try:
            tz = ZoneInfo(self.calendar_tools.config.timezone)
            time_min_dt = datetime.datetime.fromisoformat(time_min).replace(tzinfo=tz)
            time_max_dt = datetime.datetime.fromisoformat(time_max).replace(tzinfo=tz)
            
            # Default to primary calendar
            if not calendars:
                calendars = ['primary']
            
            body = {
                "timeMin": time_min_dt.isoformat(),
                "timeMax": time_max_dt.isoformat(),
                "items": [{"id": cal} for cal in calendars]
            }
            
            # Get free/busy info
            freebusy_result = self.calendar_tools.freebusy_query(body=body)
            
            output = ["Free/Busy Information:\n"]
            for calendar_id, calendar_info in freebusy_result['calendars'].items():
                output.append(f"Calendar: {calendar_id}")
                
                if 'busy' in calendar_info:
                    if calendar_info['busy']:
                        output.append("  Busy times:")
                        for busy_period in calendar_info['busy']:
                            output.append(f"    - {busy_period['start']} to {busy_period['end']}")
                    else:
                        output.append("  No busy times in this period")
                
                if 'errors' in calendar_info:
                    output.append(f"  Errors: {calendar_info['errors']}")
            
            return "\n".join(output)
            
        except Exception as e:
            return f"Error getting free/busy information: {str(e)}"


class ListCalendarsTool(BaseTool):
    """Tool for listing available calendars"""
    name: str = "list_calendars"
    description: str = "List all available Google Calendars"
    calendar_tools: 'GoogleCalendarTools'
    
    def _run(self) -> str:
        """List available calendars"""
        try:
            calendar_list = self.calendar_tools.calendar_list_list()
            
            output = ["Available Calendars:\n"]
            for calendar in calendar_list.get('items', []):
                cal_info = f"- {calendar['summary']}"
                cal_info += f"\n  ID: {calendar['id']}"
                cal_info += f"\n  Access Role: {calendar['accessRole']}"
                
                if 'description' in calendar:
                    cal_info += f"\n  Description: {calendar['description']}"
                
                if calendar.get('primary'):
                    cal_info += "\n  (Primary Calendar)"
                
                output.append(cal_info)
            
            return "\n".join(output)
            
        except Exception as e:
            return f"Error listing calendars: {str(e)}"


class CalendarUnifiedTool(BaseTool):
    """Unified Google Calendar tool with action parameter"""
    name: str = "calendar"
    description: str = (
        "Unified Google Calendar tool. Actions: create | list | get | update | delete | search | freebusy | list_calendars. "
        "Use create to make events; list/search to browse; get/update/delete by event_id; freebusy for availability; list_calendars to enumerate calendars."
    )
    args_schema: Type[BaseModel] = CalendarUnifiedInput
    calendar_tools: 'GoogleCalendarTools'

    def _run(
        self,
        action: str,
        summary: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        reminder_minutes: Optional[int] = 10,
        max_results: Optional[int] = 10,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        query: Optional[str] = None,
        event_id: Optional[str] = None,
        calendars: Optional[List[str]] = None,
    ) -> str:
        a = (action or "").strip().lower()
        if a == "create":
            if not (summary and start_time and end_time):
                return "Calendar create failed: missing summary/start_time/end_time"
            return CreateEventTool(calendar_tools=self.calendar_tools)._run(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
                attendees=attendees,
                reminder_minutes=reminder_minutes or 10,
            )
        if a == "list":
            return ListEventsTool(calendar_tools=self.calendar_tools)._run(
                max_results=max_results or 10,
                time_min=time_min,
                time_max=time_max,
                query=query,
            )
        if a == "search":
            return SearchEventsTool(calendar_tools=self.calendar_tools)._run(
                query=query or "",
                max_results=max_results or 10,
                time_min=time_min,
                time_max=time_max,
            )
        if a == "get":
            if not event_id:
                return "Calendar get failed: missing event_id"
            return GetEventTool(calendar_tools=self.calendar_tools)._run(event_id=event_id)
        if a == "update":
            if not event_id:
                return "Calendar update failed: missing event_id"
            return UpdateEventTool(calendar_tools=self.calendar_tools)._run(
                event_id=event_id,
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
            )
        if a == "delete":
            if not event_id:
                return "Calendar delete failed: missing event_id"
            return DeleteEventTool(calendar_tools=self.calendar_tools)._run(event_id=event_id)
        if a == "freebusy":
            if not (time_min and time_max):
                return "Calendar freebusy failed: missing time_min/time_max"
            return GetFreeBusyTool(calendar_tools=self.calendar_tools)._run(
                time_min=time_min,
                time_max=time_max,
                calendars=calendars,
            )
        if a == "list_calendars":
            return ListCalendarsTool(calendar_tools=self.calendar_tools)._run()
        return "Calendar tool failed: unknown action (use create|list|get|update|delete|search|freebusy|list_calendars)"


# Example usage and initialization
def initialize_calendar_tools(
    credentials_file: str = "credentials.json",
    timezone: str = "Asia/Jakarta",
    token_file: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[BaseTool]:
    """Initialize and return Google Calendar tools for LangChain

    Args:
        credentials_file: Path to Google OAuth2 credentials file
        timezone: Timezone for calendar events

    Returns:
        List of LangChain tools for Google Calendar
    """
    config = CalendarConfig(
        credentials_file=credentials_file,
        # Leave token_file blank unless explicitly provided so we can derive provider-specific path
        token_file=(token_file or ""),
        timezone=timezone,
        agent_id=agent_id,
    )

    try:
        calendar_tools = GoogleCalendarTools(config)
        tools = calendar_tools.get_langchain_tools()
        return tools
    except Exception as e:
        # Return stub tools instead of raising exception
        return _create_stub_tools(str(e))


def _create_stub_tools(error_msg: str) -> List[BaseTool]:
    """Create stub tools when calendar initialization fails"""
    try:
        from langchain_core.tools import Tool as CoreTool  # type: ignore
    except Exception:  # pragma: no cover
        from langchain.agents import Tool as CoreTool  # type: ignore

    def _stub(_input: str = "") -> str:
        return f"Google Calendar tool unavailable: {error_msg}. Check credentials and OAuth setup."

    stub_tools = []
    for tool_name in [
        "calendar",
        "create_calendar_event",
        "list_calendar_events",
        "get_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "search_calendar_events",
        "get_free_busy",
        "list_calendars",
    ]:
        stub_tools.append(CoreTool(
            name=tool_name,
            description=f"Google Calendar tool (unavailable: {error_msg})",
            func=_stub
        ))

    print(f"[DEBUG] Created {len(stub_tools)} stub calendar tools due to error: {error_msg}")
    return stub_tools


if __name__ == "__main__":
    # Example: Initialize tools
    try:
        tools = initialize_calendar_tools()
        print(f"Initialized {len(tools)} Google Calendar tools:")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description}")
    except Exception as e:
        print(f"Error initializing tools: {e}")
        print("\nMake sure you have:")
        print("1. Created a Google Cloud project")
        print("2. Enabled Google Calendar API")
        print("3. Downloaded credentials.json")
        print("4. Installed required packages:")
        print("   pip install google-auth google-auth-oauthlib google-auth-httplib2")
        print("   pip install google-api-python-client")
        print("   pip install langchain langchain-community")
