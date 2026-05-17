from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health_check(request: Request):
    engine = request.app.state.engine
    return {
        "status": "ok",
        "engine_running": engine._running,
        "last_poll": engine.last_poll_time.isoformat() if engine.last_poll_time else None,
        "total_devices_tracked": len(engine.device_positions),
        "rooms_monitored": len(engine.room_states),
        "ap_status": engine.ap_status,
    }


@router.get("/debug/triangulation")
async def debug_triangulation(request: Request):
    """
    Debug endpoint: shows for each positioned device which APs saw it,
    with what RSSI, and the resulting computed position. Use this to verify
    triangulation is working.
    """
    engine = request.app.state.engine
    devices = []
    for mac, pos in engine.device_positions.items():
        obs = engine.device_observations.get(mac, [])
        devices.append({
            "mac": mac,
            "position": {"x": pos["x"], "y": pos["y"], "confidence": pos.get("confidence", 0)},
            "room_id": pos.get("room_id"),
            "ap_count": len(obs),
            "observations": [
                {"ap_id": o["ap_id"], "ap_name": o["ap_name"], "rssi_dbm": o["rssi"]}
                for o in obs
            ],
        })

    # Sort by AP count (most-triangulatable first), then by best RSSI
    devices.sort(key=lambda d: (
        -d["ap_count"],
        min((o["rssi_dbm"] for o in d["observations"]), default=-100),
    ))

    triangulatable = sum(1 for d in devices if d["ap_count"] >= 2)

    return {
        "total_devices": len(devices),
        "triangulatable_count": triangulatable,
        "single_ap_count": len(devices) - triangulatable,
        "devices": devices,
    }


@router.get("/debug/calendar")
async def debug_calendar(request: Request):
    """Debug: show last calendar push state and timestamps."""
    store = getattr(request.app.state, "calendar_store", {})
    return {
        "calendars_received": len(store),
        "store": store,
    }


@router.get("/debug/find/{mac}")
async def debug_find_mac(mac: str, request: Request):
    """
    Search for a specific MAC across all APs' raw last scan, regardless of threshold.
    URL-friendly format works: 34CFF6E07383, 34:CF:F6:E0:73:83, 34-cf-f6-e0-73-83
    """
    # Normalize MAC: strip separators, lowercase
    target = mac.lower().replace(":", "").replace("-", "")
    if len(target) != 12 or not all(c in "0123456789abcdef" for c in target):
        return {"error": "Invalid MAC format. Use 12 hex chars or aa:bb:cc:dd:ee:ff"}

    target_colon = ":".join(target[i:i+2] for i in range(0, 12, 2))

    engine = request.app.state.engine
    raw_reports = getattr(engine, "last_raw_reports", {})

    sightings = []
    for ap_id, report in raw_reports.items():
        for client in report.associated_clients:
            client_mac = client.mac.lower().replace(":", "").replace("-", "")
            if client_mac == target:
                sightings.append({
                    "ap_id": ap_id,
                    "ap_ip": report.ap_id,
                    "rssi_dbm": client.rssi,
                    "connected_time_sec": client.connected_time,
                })

    # Also check positioned devices and observations
    pos = engine.device_positions.get(target_colon)
    obs = engine.device_observations.get(target_colon, [])

    return {
        "searched_for": target_colon,
        "sightings_in_last_poll": sightings,
        "ap_count_in_last_poll": len(sightings),
        "is_above_engine_threshold": any(s["rssi_dbm"] > -75 for s in sightings),
        "currently_positioned": pos is not None,
        "current_position": pos,
        "current_observations": obs,
    }


@router.get("/debug/all_macs")
async def debug_all_macs(request: Request, min_rssi: int = -85):
    """
    Dump every MAC seen by any AP in last poll, with all RSSI values.
    Useful for finding why a device isn't showing up.
    """
    engine = request.app.state.engine
    raw_reports = getattr(engine, "last_raw_reports", {})

    # mac -> list of (ap_id, rssi)
    mac_map: dict = {}
    for ap_id, report in raw_reports.items():
        for client in report.associated_clients:
            if client.rssi < min_rssi:
                continue
            mac_lower = client.mac.lower()
            mac_map.setdefault(mac_lower, []).append({
                "ap_id": ap_id,
                "ap_ip": report.ap_id,
                "rssi": client.rssi,
            })

    devices = [
        {
            "mac": mac,
            "ap_count": len(sightings),
            "best_rssi": max(s["rssi"] for s in sightings),
            "sightings": sightings,
        }
        for mac, sightings in mac_map.items()
    ]
    devices.sort(key=lambda d: (-d["ap_count"], -d["best_rssi"]))

    return {
        "total_unique_macs": len(devices),
        "min_rssi_filter": min_rssi,
        "multi_ap_count": sum(1 for d in devices if d["ap_count"] >= 2),
        "devices": devices,
    }
