from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.database import create_room, get_rooms_for_floor, update_room, delete_room, get_floor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["rooms"])


class RoomCreate(BaseModel):
    name: str
    calendar_id: str = ""
    polygon: list[list[float]] = []
    color: str = "#4A90D9"


class RoomUpdate(BaseModel):
    name: str | None = None
    calendar_id: str | None = None
    polygon: list[list[float]] | None = None
    color: str | None = None


@router.get("/floors/{floor_id}/rooms")
async def list_rooms(floor_id: int):
    floor = await get_floor(floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found")
    rooms = await get_rooms_for_floor(floor_id)
    return {"rooms": rooms}


@router.post("/floors/{floor_id}/rooms")
async def create_new_room(floor_id: int, body: RoomCreate):
    floor = await get_floor(floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found")
    room = await create_room(
        floor_id, body.name, body.calendar_id, body.polygon, body.color
    )
    logger.info("Room created: id=%d name='%s' floor=%d", room["id"], body.name, floor_id)
    return room


@router.put("/rooms/{room_id}")
async def update_existing_room(room_id: int, body: RoomUpdate):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    room = await update_room(room_id, **updates)
    if not room:
        raise HTTPException(404, "Room not found")
    logger.info("Room updated: id=%d fields=%s", room_id, list(updates.keys()))
    return room


@router.delete("/rooms/{room_id}")
async def remove_room(room_id: int):
    deleted = await delete_room(room_id)
    if not deleted:
        raise HTTPException(404, "Room not found")
    logger.info("Room deleted: id=%d", room_id)
    return {"status": "deleted"}
