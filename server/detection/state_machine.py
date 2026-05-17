"""
Per-room Occupancy State Machine.

6 states:
    VACANT          → calendar free, no devices
    OCCUPIED        → calendar busy, devices present
    MAYBE_GHOST     → calendar busy, 0 devices (timer starts)
    GHOST_BOOKING   → 10 min in MAYBE_GHOST → release calendar, notify
    ADHOC_USE       → calendar free, >2 devices (timer starts)
    ADHOC_CONFIRMED → 5 min in ADHOC_USE → create booking, notify

Transitions:
    VACANT → OCCUPIED:       calendar busy + devices > 0
    VACANT → MAYBE_GHOST:    calendar busy + devices == 0
    VACANT → ADHOC_USE:      calendar free + devices >= threshold
    OCCUPIED → MAYBE_GHOST:  devices drop to 0 (calendar still busy)
    OCCUPIED → ADHOC_USE:    calendar freed but devices remain
    OCCUPIED → VACANT:       calendar freed + devices == 0
    MAYBE_GHOST → OCCUPIED:  devices return
    MAYBE_GHOST → GHOST:     timer expires (10 min)
    GHOST → OCCUPIED:        devices arrive (ghost resolved)
    ADHOC_USE → VACANT:      devices leave
    ADHOC_USE → ADHOC_CONFIRMED: timer expires (5 min)
    ADHOC_CONFIRMED → VACANT: devices leave
"""

from __future__ import annotations

import logging
from datetime import datetime
from .models import RoomStatus, EventType, OccupancyEvent, CalendarEvent

logger = logging.getLogger(__name__)

# Default thresholds
GHOST_TIMEOUT_MINUTES = 10
ADHOC_DEVICE_THRESHOLD = 2
ADHOC_CONFIRM_MINUTES = 5


class RoomStateMachine:
    def __init__(
        self,
        room_id: int,
        room_name: str,
        floor_name: str = "",
        ghost_timeout_min: int = GHOST_TIMEOUT_MINUTES,
        adhoc_threshold: int = ADHOC_DEVICE_THRESHOLD,
        adhoc_confirm_min: int = ADHOC_CONFIRM_MINUTES,
    ):
        self.room_id = room_id
        self.room_name = room_name
        self.floor_name = floor_name
        self.ghost_timeout_min = ghost_timeout_min
        self.adhoc_threshold = adhoc_threshold
        self.adhoc_confirm_min = adhoc_confirm_min

        self.state: RoomStatus = RoomStatus.VACANT
        self.state_entered_at: datetime = datetime.utcnow()
        self.last_device_count: int = 0
        self.last_calendar_busy: bool = False
        self.current_event: CalendarEvent | None = None

    def _transition(self, new_state: RoomStatus, now: datetime):
        if self.state != new_state:
            logger.info(
                "Room '%s': %s -> %s (devices=%d, cal_busy=%s)",
                self.room_name, self.state.value, new_state.value,
                self.last_device_count, self.last_calendar_busy,
            )
            self.state = new_state
            self.state_entered_at = now

    def _minutes_in_state(self, now: datetime) -> float:
        return (now - self.state_entered_at).total_seconds() / 60.0

    def update(
        self,
        device_count: int,
        calendar_busy: bool,
        calendar_event: CalendarEvent | None = None,
        now: datetime | None = None,
    ) -> list[OccupancyEvent]:
        """
        Update the state machine with new sensor and calendar data.

        Returns a list of events that should trigger actions (notifications, etc.)
        """
        now = now or datetime.utcnow()
        self.last_device_count = device_count
        self.last_calendar_busy = calendar_busy
        self.current_event = calendar_event
        events: list[OccupancyEvent] = []

        if self.state == RoomStatus.VACANT:
            if calendar_busy and device_count > 0:
                self._transition(RoomStatus.OCCUPIED, now)
            elif calendar_busy and device_count == 0:
                self._transition(RoomStatus.MAYBE_GHOST, now)
            elif not calendar_busy and device_count >= self.adhoc_threshold:
                self._transition(RoomStatus.ADHOC_USE, now)

        elif self.state == RoomStatus.OCCUPIED:
            if device_count == 0 and calendar_busy:
                self._transition(RoomStatus.MAYBE_GHOST, now)
            elif not calendar_busy and device_count >= self.adhoc_threshold:
                self._transition(RoomStatus.ADHOC_USE, now)
            elif not calendar_busy and device_count == 0:
                self._transition(RoomStatus.VACANT, now)
            elif not calendar_busy and 0 < device_count < self.adhoc_threshold:
                # Few people left after meeting ended - still consider occupied briefly
                self._transition(RoomStatus.ADHOC_USE, now)

        elif self.state == RoomStatus.MAYBE_GHOST:
            if device_count > 0:
                self._transition(RoomStatus.OCCUPIED, now)
            elif not calendar_busy:
                # Calendar event ended naturally while room was empty
                self._transition(RoomStatus.VACANT, now)
            elif self._minutes_in_state(now) >= self.ghost_timeout_min:
                self._transition(RoomStatus.GHOST_BOOKING, now)
                events.append(OccupancyEvent(
                    type=EventType.GHOST_DETECTED,
                    room_id=self.room_id,
                    room_name=self.room_name,
                    floor_name=self.floor_name,
                    details={
                        "event_title": calendar_event.title if calendar_event else "Unknown",
                        "organizer": calendar_event.organizer if calendar_event else "Unknown",
                        "event_id": calendar_event.event_id if calendar_event else "",
                        "empty_minutes": round(self._minutes_in_state(now), 1),
                    },
                ))

        elif self.state == RoomStatus.GHOST_BOOKING:
            if device_count > 0:
                self._transition(RoomStatus.OCCUPIED, now)
                events.append(OccupancyEvent(
                    type=EventType.GHOST_RESOLVED,
                    room_id=self.room_id,
                    room_name=self.room_name,
                    floor_name=self.floor_name,
                ))
            elif not calendar_busy:
                # Event ended (maybe we released it, or it ended naturally)
                self._transition(RoomStatus.VACANT, now)

        elif self.state == RoomStatus.ADHOC_USE:
            if device_count == 0:
                self._transition(RoomStatus.VACANT, now)
            elif calendar_busy and device_count > 0:
                # Someone booked the room while people were in it
                self._transition(RoomStatus.OCCUPIED, now)
            elif self._minutes_in_state(now) >= self.adhoc_confirm_min:
                self._transition(RoomStatus.ADHOC_CONFIRMED, now)
                events.append(OccupancyEvent(
                    type=EventType.ADHOC_DETECTED,
                    room_id=self.room_id,
                    room_name=self.room_name,
                    floor_name=self.floor_name,
                    details={"device_count": device_count},
                ))

        elif self.state == RoomStatus.ADHOC_CONFIRMED:
            if device_count == 0:
                self._transition(RoomStatus.VACANT, now)
                events.append(OccupancyEvent(
                    type=EventType.ADHOC_ENDED,
                    room_id=self.room_id,
                    room_name=self.room_name,
                    floor_name=self.floor_name,
                ))
            elif calendar_busy:
                # Room got booked (possibly auto-booked by us)
                self._transition(RoomStatus.OCCUPIED, now)

        return events

    def to_dict(self) -> dict:
        return {
            "room_id": self.room_id,
            "room_name": self.room_name,
            "state": self.state.value,
            "state_entered_at": self.state_entered_at.isoformat(),
            "device_count": self.last_device_count,
            "calendar_busy": self.last_calendar_busy,
            "current_event": {
                "title": self.current_event.title,
                "organizer": self.current_event.organizer,
                "start": self.current_event.start.isoformat(),
                "end": self.current_event.end.isoformat(),
            } if self.current_event else None,
            "minutes_in_state": round(
                (datetime.utcnow() - self.state_entered_at).total_seconds() / 60, 1
            ),
        }
