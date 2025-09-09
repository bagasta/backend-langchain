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
from typing import Dict, List, Optional, Any, Union, Type
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from langchain.tools import BaseTool
# Prefer native Pydantic v2; fall back to v1 compatibility if needed
try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field  # type: ignore

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request, AuthorizedSession
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
        self._initialize_service()
    
    def _initialize_service(self):
        """Initialize Google Calendar service"""
        try:
            creds = self._get_credentials()
            try:
                # Prefer discovery client when available
                self.service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
                logger.info("Google Calendar service initialized successfully")
            except Exception as build_exc:
                # Fallback to direct REST session if discovery fails (e.g., UnknownApiNameOrVersion)
                logger.warning(f"Calendar discovery client unavailable ({build_exc}); falling back to REST session")
                self.session = AuthorizedSession(creds)
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar service: {e}")
            # If credentials are ok but discovery failed, we may still have a REST session
            if self.session is None:
                raise

    # -----------------------------
    # REST fallback helpers
    # -----------------------------
    @property
    def _base_url(self) -> str:
        return "https://www.googleapis.com/calendar/v3"

    def events_list(self, **params) -> Dict[str, Any]:
        if self.service:
            return self.service.events().list(**params).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        calendar_id = params.pop('calendarId', 'primary')
        resp = self.session.get(f"{self._base_url}/calendars/{calendar_id}/events", params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def events_get(self, calendarId: str, eventId: str) -> Dict[str, Any]:
        if self.service:
            return self.service.events().get(calendarId=calendarId, eventId=eventId).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.get(f"{self._base_url}/calendars/{calendarId}/events/{eventId}", timeout=20)
        resp.raise_for_status()
        return resp.json()

    def events_insert(self, calendarId: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.service:
            return self.service.events().insert(calendarId=calendarId, body=body).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.post(f"{self._base_url}/calendars/{calendarId}/events", json=body, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def events_update(self, calendarId: str, eventId: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.service:
            return self.service.events().update(calendarId=calendarId, eventId=eventId, body=body).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.put(f"{self._base_url}/calendars/{calendarId}/events/{eventId}", json=body, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def events_delete(self, calendarId: str, eventId: str) -> None:
        if self.service:
            self.service.events().delete(calendarId=calendarId, eventId=eventId).execute()
            return
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.delete(f"{self._base_url}/calendars/{calendarId}/events/{eventId}", timeout=20)
        # Google returns 204 No Content on success
        if not (200 <= resp.status_code < 300):
            resp.raise_for_status()

    def freebusy_query(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.service:
            return self.service.freebusy().query(body=body).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.post(f"{self._base_url}/freeBusy", json=body, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def calendar_list_list(self, **params) -> Dict[str, Any]:
        if self.service:
            return self.service.calendarList().list(**params).execute()
        if not self.session:
            raise RuntimeError("Calendar client not initialized")
        resp = self.session.get(f"{self._base_url}/users/me/calendarList", params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()
    
    def _get_credentials(self) -> Credentials:
        """Get or refresh Google Calendar credentials
        
        Returns:
            Credentials object for Google Calendar API
        """
        creds = None
        
        # Load existing token
        if os.path.exists(self.config.token_file):
            creds = Credentials.from_authorized_user_file(self.config.token_file, SCOPES)
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
                    f"Google Calendar token at {self.config.token_file} is not authorized for required scopes. "
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
                raise RuntimeError(
                    "Google Calendar OAuth token not found or invalid at "
                    f"{self.config.token_file}. Please authorize via the Calendar OAuth URL and retry."
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
            # Parse times and add timezone
            tz = ZoneInfo(self.calendar_tools.config.timezone)
            start_dt = datetime.datetime.fromisoformat(start_time).replace(tzinfo=tz)
            end_dt = datetime.datetime.fromisoformat(end_time).replace(tzinfo=tz)
            
            # Build event body
            event = {
                'summary': summary,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': self.calendar_tools.config.timezone,
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': self.calendar_tools.config.timezone,
                }
            }
            
            if description:
                event['description'] = description
            if location:
                event['location'] = location
            if attendees:
                event['attendees'] = [{'email': email} for email in attendees]
            if reminder_minutes:
                event['reminders'] = {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': reminder_minutes},
                    ],
                }
            
            # Create event
            result = self.calendar_tools.events_insert(
                calendarId='primary',
                body=event,
            )
            
            return f"Event created successfully: {result.get('htmlLink')}"
            
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
            return f"Error getting event: {str(e)}"


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
            # Get existing event
            event = self.calendar_tools.events_get(
                calendarId='primary',
                eventId=event_id,
            )
            
            # Update fields if provided
            if summary:
                event['summary'] = summary
            if description is not None:
                event['description'] = description
            if location is not None:
                event['location'] = location
            
            if start_time:
                tz = ZoneInfo(self.calendar_tools.config.timezone)
                start_dt = datetime.datetime.fromisoformat(start_time).replace(tzinfo=tz)
                event['start'] = {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': self.calendar_tools.config.timezone,
                }
            
            if end_time:
                tz = ZoneInfo(self.calendar_tools.config.timezone)
                end_dt = datetime.datetime.fromisoformat(end_time).replace(tzinfo=tz)
                event['end'] = {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': self.calendar_tools.config.timezone,
                }
            
            # Update event
            updated_event = self.calendar_tools.events_update(
                calendarId='primary',
                eventId=event_id,
                body=event,
            )
            
            return f"Event updated successfully: {updated_event.get('htmlLink')}"
            
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
            self.calendar_tools.events_delete(
                calendarId='primary',
                eventId=event_id,
            )
            
            return f"Event {event_id} deleted successfully"
            
        except Exception as e:
            return f"Error deleting event: {str(e)}"


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
    )
    
    calendar_tools = GoogleCalendarTools(config)
    return calendar_tools.get_langchain_tools()


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
