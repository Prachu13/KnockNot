from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from server.database import get_floor, get_rooms_for_floor, get_aps_for_floor
from server.api.demo import apply_overrides_to_heatmap

router = APIRouter(prefix="/api/v1", tags=["heatmap"])


@router.get("/floors/{floor_id}/heatmap")
async def get_floor_heatmap(floor_id: int, request: Request):
    """Get live heatmap data for a floor (device positions + room states)."""
    floor = await get_floor(floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found")

    rooms = await get_rooms_for_floor(floor_id)
    aps = await get_aps_for_floor(floor_id)

    engine = request.app.state.engine
    heatmap = engine.get_floor_heatmap(floor_id, rooms, aps)

    response = {
        "floor": {
            "id": floor["id"],
            "name": floor["name"],
            "width_meters": floor["width_meters"],
            "height_meters": floor["height_meters"],
            "image_path": floor["image_path"],
        },
        **heatmap,
    }

    # Apply demo overrides (if any) — for presentations
    response = apply_overrides_to_heatmap(response, request.app.state)

    return response
