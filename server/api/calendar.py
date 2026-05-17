from __future__ import annotations

import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel

from server.database import get_all_rooms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


class CalendarEventData(BaseModel):
    id: str = ""
    title: str = ""
    organizer: str = ""
    start: str = ""
    end: str = ""


class RoomCalendarStatus(BaseModel):
    calendar_id: str
    busy: bool = False
    event: CalendarEventData | None = None
    error: str | None = None


class CalendarPush(BaseModel):
    timestamp: str
    rooms: list[RoomCalendarStatus]


class CalendarInfo(BaseModel):
    id: str
    name: str
    is_resource: bool = False


class CalendarDiscovery(BaseModel):
    calendars: list[CalendarInfo]


@router.get("/rooms")
async def get_calendar_room_ids():
    """
    Returns the list of calendar_ids the server needs checked.
    Called by the Apps Script to know which calendars to query.
    """
    rooms = await get_all_rooms()
    calendar_ids = [
        r["calendar_id"]
        for r in rooms
        if r.get("calendar_id")
    ]
    # Deduplicate
    calendar_ids = list(set(calendar_ids))
    return {"calendar_ids": calendar_ids}


@router.post("/push")
async def receive_calendar_push(body: CalendarPush, request: Request):
    """
    Receives calendar status pushed from the Apps Script.
    Stores the latest status in app state for the engine to read.
    """
    store = request.app.state.calendar_store
    for room_status in body.rooms:
        store[room_status.calendar_id] = {
            "busy": room_status.busy,
            "event": room_status.event.dict() if room_status.event else None,
            "error": room_status.error,
            "updated_at": body.timestamp,
        }

    logger.info(
        "Calendar push received: %d rooms (%d busy)",
        len(body.rooms),
        sum(1 for r in body.rooms if r.busy),
    )
    return {"status": "ok", "rooms_updated": len(body.rooms)}


@router.post("/discovered")
async def receive_discovered_calendars(body: CalendarDiscovery, request: Request):
    """
    Receives the full list of calendars discovered by the Apps Script.
    Stored so the dashboard can show available room calendars.
    """
    request.app.state.discovered_calendars = [c.dict() for c in body.calendars]
    resource_count = sum(1 for c in body.calendars if c.is_resource)
    logger.info(
        "Calendar discovery received: %d calendars (%d room resources)",
        len(body.calendars), resource_count,
    )
    return {"status": "ok", "calendars": len(body.calendars)}


@router.get("/discovered")
async def get_discovered_calendars(request: Request):
    """Returns the list of calendars discovered by the Apps Script."""
    cals = getattr(request.app.state, "discovered_calendars", [])
    return {"calendars": cals}
