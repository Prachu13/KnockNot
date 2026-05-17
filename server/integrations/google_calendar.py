"""
Google Calendar integration — READ-ONLY.

Reads room calendar status from data pushed by the Apps Script.
The Apps Script runs inside Google Workspace on a 1-minute trigger
and POSTs room status to /api/v1/calendar/push.

This module simply reads the latest pushed state from app.state.calendar_store.
No direct Google API calls, no write operations.
"""

from __future__ import annotations

import logging
from datetime import datetime

from server.detection.models import CalendarEvent

logger = logging.getLogger(__name__)

# Reference to the FastAPI app state — set during engine init
_app_state = None


def set_app_state(state):
    """Called by the engine to provide access to app.state.calendar_store."""
    global _app_state
    _app_state = state


async def get_room_status(calendar_id: str) -> tuple[bool, CalendarEvent | None]:
    """
    Check if a room calendar is currently busy.
    Reads from the calendar_store populated by the Apps Script push.
    """
    if not _app_state:
        return (False, None)

    store = getattr(_app_state, "calendar_store", {})
    entry = store.get(calendar_id)

    if not entry:
        logger.debug("No calendar data for %s (Apps Script may not have pushed yet)", calendar_id)
        return (False, None)

    if not entry.get("busy"):
        return (False, None)

    logger.info("Calendar %s: BUSY — '%s'", calendar_id, entry.get("event", {}).get("title", "?"))

    ev_data = entry.get("event")
    if not ev_data:
        return (True, None)

    try:
        cal_event = CalendarEvent(
            event_id=ev_data.get("id", ""),
            title=ev_data.get("title", "No title"),
            organizer=ev_data.get("organizer", "Unknown"),
            start=datetime.fromisoformat(ev_data["start"].replace("Z", "+00:00")),
            end=datetime.fromisoformat(ev_data["end"].replace("Z", "+00:00")),
        )
        return (True, cal_event)
    except Exception as e:
        logger.error("Failed to parse calendar event for %s: %s", calendar_id, e)
        return (True, None)
