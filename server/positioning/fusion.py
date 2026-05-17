"""
Temporal smoothing and position fusion.

Maintains a rolling window of position readings per device to:
- Prevent flicker (someone steps out for 30s to refill water)
- Smooth noisy RSSI-based positions
- Provide stable room assignment

Rules:
- Device "in room R" if present in R in 2+ of last 4 readings
- Device "left room R" if absent from R in all last 4 readings
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime


WINDOW_SIZE = 4  # Number of readings to keep per device
MIN_PRESENT_COUNT = 2  # Minimum readings in window to be "in room"


class PresenceSmoother:
    """Temporal smoothing for per-device room presence."""

    def __init__(self, window_size: int = WINDOW_SIZE, min_present: int = MIN_PRESENT_COUNT):
        self.window_size = window_size
        self.min_present = min_present
        # mac → list of (room_id_or_none, timestamp) tuples
        self._history: dict[str, list[tuple[int | None, datetime]]] = defaultdict(list)

    def update(self, device_mac: str, room_id: int | None, timestamp: datetime | None = None):
        """Record a new position reading for a device."""
        ts = timestamp or datetime.utcnow()
        mac = device_mac.lower()
        history = self._history[mac]
        history.append((room_id, ts))
        # Trim to window size
        if len(history) > self.window_size:
            self._history[mac] = history[-self.window_size:]

    def get_stable_room(self, device_mac: str) -> int | None:
        """
        Get the smoothed/stable room assignment for a device.

        Returns room_id if the device has been in that room for enough
        readings in the window, otherwise None.
        """
        mac = device_mac.lower()
        history = self._history.get(mac, [])
        if not history:
            return None

        # Count occurrences of each room in the window
        room_counts: dict[int | None, int] = defaultdict(int)
        for room_id, _ in history:
            room_counts[room_id] += 1

        # Find the most frequently assigned room (excluding None)
        best_room = None
        best_count = 0
        for room_id, count in room_counts.items():
            if room_id is not None and count > best_count:
                best_room = room_id
                best_count = count

        # Only return if device has been in this room enough times
        if best_count >= self.min_present:
            return best_room
        return None

    def get_all_stable_assignments(self) -> dict[str, int | None]:
        """Get stable room assignments for all tracked devices."""
        return {
            mac: self.get_stable_room(mac)
            for mac in self._history
        }

    def get_room_device_counts(self, room_ids: list[int]) -> dict[int, int]:
        """Get smoothed device count per room."""
        counts = {rid: 0 for rid in room_ids}
        for mac in self._history:
            room_id = self.get_stable_room(mac)
            if room_id is not None and room_id in counts:
                counts[room_id] += 1
        return counts

    def cleanup_stale(self, max_age_seconds: int = 300):
        """Remove devices not seen in the last N seconds."""
        now = datetime.utcnow()
        stale_macs = []
        for mac, history in self._history.items():
            if history:
                _, last_ts = history[-1]
                if (now - last_ts).total_seconds() > max_age_seconds:
                    stale_macs.append(mac)
        for mac in stale_macs:
            del self._history[mac]
