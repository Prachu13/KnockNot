"""
Demo Override API.

Lets you manually force a room's state and device count for demos/presentations.
Overrides take precedence over real triangulation data in the heatmap response.

Use cases:
  - Force a room to OCCUPIED with N devices for a demo
  - Force a room to VACANT to hide false positives
  - Reset to live data when done

Endpoints:
  POST /api/v1/demo/override   — set overrides
  GET  /api/v1/demo/override   — list active overrides
  POST /api/v1/demo/clear      — remove all overrides
"""

from __future__ import annotations

import logging
import random
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class RoomOverride(BaseModel):
    room_name: str
    device_count: int = 0
    state: str | None = None  # If None, derived from device_count


class OverrideRequest(BaseModel):
    overrides: list[RoomOverride]


@router.get("/override")
async def list_overrides(request: Request):
    """Return currently active overrides."""
    overrides = getattr(request.app.state, "demo_overrides", {})
    return {"overrides": overrides, "count": len(overrides)}


@router.post("/override")
async def set_overrides(body: OverrideRequest, request: Request):
    """
    Set room overrides. Replaces any existing override for the same room.
    Existing overrides for OTHER rooms are preserved.
    """
    if not hasattr(request.app.state, "demo_overrides"):
        request.app.state.demo_overrides = {}

    overrides = request.app.state.demo_overrides

    for ov in body.overrides:
        # Auto-derive state from device count if not specified
        state = ov.state
        if state is None:
            state = "OCCUPIED" if ov.device_count > 0 else "VACANT"

        overrides[ov.room_name.lower()] = {
            "device_count": ov.device_count,
            "state": state,
        }
        logger.info(
            "Demo override: room='%s' state=%s devices=%d",
            ov.room_name, state, ov.device_count,
        )

    return {"status": "ok", "active": len(overrides), "overrides": overrides}


@router.post("/clear")
async def clear_overrides(request: Request):
    """Remove all overrides — back to live triangulation data."""
    if hasattr(request.app.state, "demo_overrides"):
        count = len(request.app.state.demo_overrides)
        request.app.state.demo_overrides = {}
        logger.info("Demo overrides cleared (%d removed)", count)
        return {"status": "cleared", "removed": count}
    return {"status": "ok", "removed": 0}


def apply_overrides_to_heatmap(heatmap: dict, app_state) -> dict:
    """
    Apply demo overrides to a heatmap response.
    Called from the heatmap endpoint just before returning.
    """
    overrides = getattr(app_state, "demo_overrides", {})
    if not overrides:
        return heatmap

    for room in heatmap.get("rooms", []):
        ov = overrides.get(room["name"].lower())
        if ov is None:
            continue

        room["state"] = ov["state"]
        room["device_count"] = ov["device_count"]

        # Generate synthetic device dots scattered inside the room polygon
        polygon = room.get("polygon", [])
        if polygon and ov["device_count"] > 0:
            room["devices"] = _scatter_dots_in_polygon(polygon, ov["device_count"], room["name"])
        else:
            room["devices"] = []

    # Recalculate total devices
    room_total = sum(len(r["devices"]) for r in heatmap.get("rooms", []))
    hallway_total = len(heatmap.get("hallway_devices", []))
    heatmap["total_devices"] = room_total + hallway_total

    return heatmap


def _scatter_dots_in_polygon(polygon: list[list[float]], count: int, seed_str: str) -> list[dict]:
    """Place N device dots randomly inside a polygon's bounding box."""
    if not polygon:
        return []

    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Padding so dots aren't exactly on the edge
    pad_x = (max_x - min_x) * 0.1
    pad_y = (max_y - min_y) * 0.1

    rng = random.Random(seed_str)  # deterministic per room — dots don't jiggle
    dots = []
    for i in range(count):
        x = rng.uniform(min_x + pad_x, max_x - pad_x)
        y = rng.uniform(min_y + pad_y, max_y - pad_y)
        # Use a stable hash combining seed_str and index so animation is consistent
        mac_hash = (hash(seed_str) ^ (i * 0x9E3779B9)) & 0xFFFFFFFF
        dots.append({"mac_hash": mac_hash, "x": x, "y": y})
    return dots
