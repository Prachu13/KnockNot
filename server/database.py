from __future__ import annotations

import aiosqlite
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

DATABASE_PATH = os.getenv("DATABASE_PATH", "data/presence.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS floors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL,
    width_meters REAL NOT NULL,
    height_meters REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    calendar_id TEXT NOT NULL DEFAULT '',
    polygon_json TEXT NOT NULL DEFAULT '[]',
    color TEXT NOT NULL DEFAULT '#4A90D9',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS aps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    mac TEXT NOT NULL DEFAULT '',
    x_meters REAL NOT NULL DEFAULT 0,
    y_meters REAL NOT NULL DEFAULT 0,
    rssi_ref REAL NOT NULL DEFAULT -42,
    path_loss_exp REAL NOT NULL DEFAULT 3.0,
    ftm_capable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS presence_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    state TEXT NOT NULL,
    device_count INTEGER NOT NULL DEFAULT 0
);
"""


async def init_db():
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info("Database initialized at %s", DATABASE_PATH)


# --- Floor operations ---

async def create_floor(name: str, location: str, image_path: str, width_meters: float, height_meters: float) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO floors (name, location, image_path, width_meters, height_meters) VALUES (?, ?, ?, ?, ?)",
            (name, location, image_path, width_meters, height_meters),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM floors WHERE id = ?", (cursor.lastrowid,))).fetchone()
        return dict(row)


async def get_floors() -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM floors ORDER BY created_at DESC")).fetchall()
        return [dict(r) for r in rows]


async def get_floor(floor_id: int) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM floors WHERE id = ?", (floor_id,))).fetchone()
        return dict(row) if row else None


async def delete_floor(floor_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("DELETE FROM floors WHERE id = ?", (floor_id,))
        await db.commit()
        return cursor.rowcount > 0


# --- Room operations ---

async def create_room(floor_id: int, name: str, calendar_id: str, polygon: list, color: str = "#4A90D9") -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO rooms (floor_id, name, calendar_id, polygon_json, color) VALUES (?, ?, ?, ?, ?)",
            (floor_id, name, calendar_id, json.dumps(polygon), color),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM rooms WHERE id = ?", (cursor.lastrowid,))).fetchone()
        return _parse_room(row)


async def get_rooms_for_floor(floor_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM rooms WHERE floor_id = ?", (floor_id,))).fetchall()
        return [_parse_room(r) for r in rows]


async def get_all_rooms() -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM rooms")).fetchall()
        return [_parse_room(r) for r in rows]


async def update_room(room_id: int, **kwargs) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k == "polygon":
                sets.append("polygon_json = ?")
                vals.append(json.dumps(v))
            else:
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.append(room_id)
        await db.execute(f"UPDATE rooms SET {', '.join(sets)} WHERE id = ?", vals)
        await db.commit()
        row = await (await db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))).fetchone()
        return _parse_room(row) if row else None


async def delete_room(room_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        await db.commit()
        return cursor.rowcount > 0


def _parse_room(row) -> dict:
    d = dict(row)
    d["polygon"] = json.loads(d.pop("polygon_json"))
    return d


# --- AP operations ---

async def create_ap(floor_id: int, name: str, ip_address: str, mac: str, x_meters: float, y_meters: float,
                    rssi_ref: float = -42, path_loss_exp: float = 3.0, ftm_capable: bool = False) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO aps (floor_id, name, ip_address, mac, x_meters, y_meters, rssi_ref, path_loss_exp, ftm_capable) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (floor_id, name, ip_address, mac, x_meters, y_meters, rssi_ref, path_loss_exp, int(ftm_capable)),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM aps WHERE id = ?", (cursor.lastrowid,))).fetchone()
        return _parse_ap(row)


async def get_aps_for_floor(floor_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM aps WHERE floor_id = ?", (floor_id,))).fetchall()
        return [_parse_ap(r) for r in rows]


async def get_all_aps() -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM aps")).fetchall()
        return [_parse_ap(r) for r in rows]


async def update_ap(ap_id: int, **kwargs) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k == "ftm_capable":
                sets.append("ftm_capable = ?")
                vals.append(int(v))
            else:
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.append(ap_id)
        await db.execute(f"UPDATE aps SET {', '.join(sets)} WHERE id = ?", vals)
        await db.commit()
        row = await (await db.execute("SELECT * FROM aps WHERE id = ?", (ap_id,))).fetchone()
        return _parse_ap(row) if row else None


async def delete_ap(ap_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("DELETE FROM aps WHERE id = ?", (ap_id,))
        await db.commit()
        return cursor.rowcount > 0


def _parse_ap(row) -> dict:
    d = dict(row)
    d["ftm_capable"] = bool(d["ftm_capable"])
    return d


# --- Presence log ---

async def log_presence(room_id: int, state: str, device_count: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO presence_log (room_id, state, device_count) VALUES (?, ?, ?)",
            (room_id, state, device_count),
        )
        await db.commit()
