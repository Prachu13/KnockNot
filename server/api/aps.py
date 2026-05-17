from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from server.database import create_ap, get_aps_for_floor, update_ap, delete_ap, get_floor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["aps"])


class APCreate(BaseModel):
    name: str
    ip_address: str
    mac: str = ""
    x_meters: float = 0
    y_meters: float = 0
    rssi_ref: float = -42
    path_loss_exp: float = 3.0
    ftm_capable: bool = False


class APUpdate(BaseModel):
    name: str | None = None
    ip_address: str | None = None
    mac: str | None = None
    x_meters: float | None = None
    y_meters: float | None = None
    rssi_ref: float | None = None
    path_loss_exp: float | None = None
    ftm_capable: bool | None = None


@router.get("/floors/{floor_id}/aps")
async def list_aps(floor_id: int):
    floor = await get_floor(floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found")
    aps = await get_aps_for_floor(floor_id)
    return {"aps": aps}


@router.post("/floors/{floor_id}/aps")
async def create_new_ap(floor_id: int, body: APCreate):
    floor = await get_floor(floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found")
    ap = await create_ap(
        floor_id, body.name, body.ip_address, body.mac,
        body.x_meters, body.y_meters,
        body.rssi_ref, body.path_loss_exp, body.ftm_capable,
    )
    logger.info("AP created: id=%d name='%s' ip=%s floor=%d", ap["id"], body.name, body.ip_address, floor_id)
    return ap


@router.put("/aps/{ap_id}")
async def update_existing_ap(ap_id: int, body: APUpdate):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    ap = await update_ap(ap_id, **updates)
    if not ap:
        raise HTTPException(404, "AP not found")
    logger.info("AP updated: id=%d fields=%s", ap_id, list(updates.keys()))
    return ap


@router.delete("/aps/{ap_id}")
async def remove_ap(ap_id: int):
    deleted = await delete_ap(ap_id)
    if not deleted:
        raise HTTPException(404, "AP not found")
    logger.info("AP deleted: id=%d", ap_id)
    return {"status": "deleted"}


@router.post("/aps/{ap_id}/test")
async def test_ap_connection(ap_id: int, request: Request):
    """Test SSH connectivity to an AP."""
    from server.database import get_all_aps
    from server.collectors.ssh_poller import poll_single_ap

    aps = await get_all_aps()
    ap = next((a for a in aps if a["id"] == ap_id), None)
    if not ap:
        raise HTTPException(404, "AP not found")

    result = await poll_single_ap(ap["ip_address"], ap_id, timeout=20)
    if result:
        return {
            "status": "connected",
            "ap_id": result.ap_id,
            "associated_clients": len(result.associated_clients),
        }
    else:
        return {"status": "failed", "message": "Could not connect to AP"}
