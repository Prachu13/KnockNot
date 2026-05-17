"""
Core Knock Not Loop.

Runs as a background asyncio task. Every cycle (30s default):
1. Load all floors/rooms/APs from database
2. SSH poll all APs concurrently
3. Compute device positions (trilateration + fusion)
4. Map devices to rooms → per-room counts
5. Fetch calendar state per room
6. Run state machine per room → events
7. Execute actions (Slack, Calendar) for events
8. Update shared app state for heatmap API
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from server.database import get_all_rooms, get_all_aps, get_floors, log_presence
from server.collectors.ssh_poller import poll_all_aps
from server.collectors.models import APReport
from server.positioning.trilateration import weighted_centroid
from server.positioning.room_mapper import assign_device_to_room
from server.positioning.fusion import PresenceSmoother
from server.detection.state_machine import RoomStateMachine
from server.detection.models import OccupancyEvent
from server.integrations.google_calendar import get_room_status, set_app_state
from server.integrations.slack_notifier import send_notification

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30


def _is_random_mac(mac: str) -> bool:
    """
    Returns True if a MAC address is locally administered (randomized).
    The locally-administered bit is bit 1 of the first octet.
    Real device MACs (universally administered) have this bit cleared.
    Hex digits 2, 6, A, E in the second position = locally administered.
    """
    if len(mac) < 2:
        return True
    second_char = mac[1].lower()
    return second_char in ('2', '6', 'a', 'e')


class PresenceEngine:
    """Main presence detection engine."""

    def __init__(self, app_state=None):
        self.smoother = PresenceSmoother()
        self.room_machines: dict[int, RoomStateMachine] = {}
        self.device_positions: dict[str, dict] = {}
        self.device_observations: dict[str, list[dict]] = {}
        self.room_states: dict[int, dict] = {}
        self.last_poll_time: datetime | None = None
        self.ap_status: dict[int, dict] = {}
        self.last_raw_reports: dict = {}  # for debug endpoints
        self._running = False
        if app_state:
            set_app_state(app_state)

    async def run(self):
        """Main loop - runs until cancelled."""
        self._running = True
        logger.info("Knock Not started (interval: %ds)", POLL_INTERVAL_SECONDS)

        while self._running:
            try:
                await self._run_cycle()
            except Exception as e:
                logger.error("Engine cycle error: %s", e, exc_info=True)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def stop(self):
        self._running = False

    async def _run_cycle(self):
        """Execute one full detection cycle."""
        now = datetime.utcnow()
        self.last_poll_time = now

        # 1. Load config from database
        floors = await get_floors()
        all_rooms = await get_all_rooms()
        all_aps = await get_all_aps()

        if not all_aps:
            logger.debug("No APs configured, skipping cycle")
            return

        # Build AP config lookup
        ap_configs = {ap["id"]: ap for ap in all_aps}

        # 2. Poll all APs concurrently
        ap_reports: dict[int, APReport] = await poll_all_aps(all_aps, timeout=60)

        # Save raw reports for debug endpoints
        self.last_raw_reports = ap_reports

        # Update AP status
        for ap in all_aps:
            ap_id = ap["id"]
            if ap_id in ap_reports:
                self.ap_status[ap_id] = {"online": True, "last_seen": now.isoformat()}
            else:
                existing = self.ap_status.get(ap_id, {})
                self.ap_status[ap_id] = {
                    "online": False,
                    "last_seen": existing.get("last_seen", "never"),
                }

        # 3. Compute device positions
        # Collect all unique device MACs across all AP reports
        # Filter: exclude random MACs (locally administered bit set)
        all_device_macs: set[str] = set()
        ap_device_data: dict[int, list[dict]] = {}

        for ap_id, report in ap_reports.items():
            devices = report.all_devices
            ap_device_data[ap_id] = devices
            for d in devices:
                mac_lower = d["mac"].lower()
                if _is_random_mac(mac_lower):
                    continue
                all_device_macs.add(mac_lower)

        # Engine-side RSSI threshold (separate from collection floor):
        # We use observations down to -78 dBm for triangulation but mark
        # devices "in room" only if at least 1 AP sees them above -65.
        TRILATERATE_FLOOR = -78  # use measurements down to this
        ROOM_PRESENCE_FLOOR = -65  # need at least one strong reading to count as "present"

        new_positions: dict[str, tuple[float, float, float]] = {}
        self.device_observations: dict[str, list[dict]] = {}

        for mac in all_device_macs:
            observations = []
            debug_obs = []
            for ap_id, devices in ap_device_data.items():
                config = ap_configs.get(ap_id)
                if not config:
                    continue
                for d in devices:
                    if d["mac"].lower() == mac and d["rssi"] >= TRILATERATE_FLOOR:
                        observations.append({
                            "x": config["x_meters"],
                            "y": config["y_meters"],
                            "rssi": d["rssi"],
                            "rssi_ref": config.get("rssi_ref", -42.0),
                            "path_loss_exp": config.get("path_loss_exp", 3.0),
                        })
                        debug_obs.append({
                            "ap_id": ap_id,
                            "ap_name": config.get("name", "?"),
                            "rssi": d["rssi"],
                        })
                        break

            if not observations:
                continue

            # Skip "in room" assignment if no strong signal — keep position for hallway viz
            best_rssi = max(o["rssi"] for o in observations)
            x, y, confidence = weighted_centroid(observations)
            new_positions[mac] = (x, y, confidence)
            self.device_observations[mac] = debug_obs

        # 4. Map devices to rooms with temporal smoothing
        # Devices with weak best-signal don't contribute to room counts (avoid false occupancy)
        for mac, (x, y, conf) in new_positions.items():
            obs = self.device_observations.get(mac, [])
            best_rssi = max((o["rssi"] for o in obs), default=-100)
            if best_rssi < ROOM_PRESENCE_FLOOR:
                # Track position but don't assign to a room
                self.smoother.update(mac, None, now)
            else:
                raw_room = assign_device_to_room(x, y, all_rooms)
                self.smoother.update(mac, raw_room, now)

        # Also mark devices NOT seen this cycle (they may have left)
        for mac in list(self.smoother._history.keys()):
            if mac not in all_device_macs:
                self.smoother.update(mac, None, now)

        # Clean up stale devices
        self.smoother.cleanup_stale(max_age_seconds=300)

        # Get smoothed room assignments
        room_device_counts = self.smoother.get_room_device_counts(
            [r["id"] for r in all_rooms]
        )

        # Update device positions for API
        self.device_positions = {}
        for mac, (x, y, conf) in new_positions.items():
            stable_room = self.smoother.get_stable_room(mac)
            self.device_positions[mac] = {
                "x": x, "y": y, "confidence": conf,
                "room_id": stable_room,
            }

        # 5 & 6. For each room: check calendar, run state machine
        for room in all_rooms:
            room_id = room["id"]
            device_count = room_device_counts.get(room_id, 0)

            # Ensure state machine exists for this room
            if room_id not in self.room_machines:
                floor = next((f for f in floors if f["id"] == room.get("floor_id")), None)
                floor_name = floor["name"] if floor else ""
                self.room_machines[room_id] = RoomStateMachine(
                    room_id=room_id,
                    room_name=room["name"],
                    floor_name=floor_name,
                )

            machine = self.room_machines[room_id]

            # Check calendar
            calendar_id = room.get("calendar_id", "")
            calendar_busy = False
            calendar_event = None
            if calendar_id:
                calendar_busy, calendar_event = await get_room_status(calendar_id)

            # Run state machine
            events = machine.update(device_count, calendar_busy, calendar_event, now)

            # Update shared state
            self.room_states[room_id] = machine.to_dict()

            # Log to database
            await log_presence(room_id, machine.state.value, device_count)

            # 7. Handle events
            for event in events:
                await self._handle_event(event)

    async def _handle_event(self, event: OccupancyEvent):
        """Process an occupancy event — notify only (read-only mode)."""
        logger.info("Event: %s for room %s (%s)",
                    event.type.value, event.room_name, event.floor_name)

        # Send Slack notification
        await send_notification(event)

    def get_floor_heatmap(self, floor_id: int, rooms: list[dict], aps: list[dict]) -> dict:
        """Get heatmap data for a specific floor."""
        floor_rooms = []
        for room in rooms:
            if room.get("floor_id") != floor_id:
                continue
            room_id = room["id"]
            state_info = self.room_states.get(room_id, {})
            # Find devices in this room
            room_devices = [
                {
                    "mac_hash": hash(mac) & 0xFFFFFFFF,  # Privacy: hash the MAC
                    "x": pos["x"],
                    "y": pos["y"],
                }
                for mac, pos in self.device_positions.items()
                if pos.get("room_id") == room_id
            ]
            floor_rooms.append({
                "room_id": room_id,
                "name": room["name"],
                "polygon": room.get("polygon", []),
                "color": room.get("color", "#4A90D9"),
                "state": state_info.get("state", "VACANT"),
                "device_count": state_info.get("device_count", 0),
                "calendar_busy": state_info.get("calendar_busy", False),
                "current_event": state_info.get("current_event"),
                "minutes_in_state": state_info.get("minutes_in_state", 0),
                "devices": room_devices,
            })

        floor_aps = []
        floor_ap_ids = set()
        for ap in aps:
            if ap.get("floor_id") != floor_id:
                continue
            ap_id = ap["id"]
            floor_ap_ids.add(ap_id)
            status = self.ap_status.get(ap_id, {"online": False, "last_seen": "never"})
            floor_aps.append({
                "ap_id": ap_id,
                "name": ap["name"],
                "x": ap["x_meters"],
                "y": ap["y_meters"],
                "ip": ap["ip_address"],
                "online": status["online"],
            })

        # Hallway / unassigned devices — positioned but not in any room polygon
        # Only include those that were seen by APs on THIS floor
        hallway_devices = []
        for mac, pos in self.device_positions.items():
            if pos.get("room_id") is not None:
                continue  # already in a room
            obs = self.device_observations.get(mac, [])
            seen_by_floor_ap = any(o["ap_id"] in floor_ap_ids for o in obs)
            if seen_by_floor_ap:
                hallway_devices.append({
                    "mac_hash": hash(mac) & 0xFFFFFFFF,
                    "x": pos["x"],
                    "y": pos["y"],
                    "confidence": pos.get("confidence", 0),
                    "ap_count": len(obs),
                })

        room_device_total = sum(len(r["devices"]) for r in floor_rooms)

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "rooms": floor_rooms,
            "aps": floor_aps,
            "hallway_devices": hallway_devices,
            "total_devices": room_device_total + len(hallway_devices),
        }
