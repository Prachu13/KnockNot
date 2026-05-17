from __future__ import annotations

import logging
import os
import uuid
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from server.database import create_floor, get_floors, get_floor, delete_floor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/floors", tags=["floors"])

UPLOAD_DIR = "uploads"


@router.get("")
async def list_floors():
    floors = await get_floors()
    return {"floors": floors}


@router.get("/{floor_id}")
async def get_floor_detail(floor_id: int):
    from server.database import get_rooms_for_floor, get_aps_for_floor
    floor = await get_floor(floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found")
    rooms = await get_rooms_for_floor(floor_id)
    aps = await get_aps_for_floor(floor_id)
    return {**floor, "rooms": rooms, "aps": aps}


@router.post("")
async def create_new_floor(
    name: str = Form(...),
    location: str = Form(""),
    width_meters: float = Form(...),
    height_meters: float = Form(...),
    image: UploadFile = File(...),
):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(image.filename or "map.png")[1] or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    content = await image.read()
    with open(filepath, "wb") as f:
        f.write(content)

    floor = await create_floor(name, location, filepath, width_meters, height_meters)
    logger.info("Floor created: id=%d name='%s' location='%s' %.0fx%.0fm", floor["id"], name, location, width_meters, height_meters)
    return floor


@router.delete("/{floor_id}")
async def remove_floor(floor_id: int):
    floor = await get_floor(floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found")
    if os.path.exists(floor["image_path"]):
        os.remove(floor["image_path"])
    await delete_floor(floor_id)
    logger.info("Floor deleted: id=%d name='%s'", floor_id, floor["name"])
    return {"status": "deleted"}
