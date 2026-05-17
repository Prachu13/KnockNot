from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RoomStatus(str, Enum):
    VACANT = "VACANT"
    OCCUPIED = "OCCUPIED"
    MAYBE_GHOST = "MAYBE_GHOST"
    GHOST_BOOKING = "GHOST_BOOKING"
    ADHOC_USE = "ADHOC_USE"
    ADHOC_CONFIRMED = "ADHOC_CONFIRMED"


class EventType(str, Enum):
    GHOST_DETECTED = "GHOST_DETECTED"
    GHOST_RESOLVED = "GHOST_RESOLVED"
    ADHOC_DETECTED = "ADHOC_DETECTED"
    ADHOC_ENDED = "ADHOC_ENDED"


@dataclass
class OccupancyEvent:
    type: EventType
    room_id: int
    room_name: str
    floor_name: str
    details: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CalendarEvent:
    event_id: str
    title: str
    organizer: str
    start: datetime
    end: datetime
