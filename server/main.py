from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from server.database import init_db
from server.engine.presence_loop import PresenceEngine
from server.api import floors, rooms, aps, heatmap, health, calendar, demo

load_dotenv()


def setup_logging():
    log_dir = os.getenv("LOG_DIR", "logs")
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(log_level)

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "presence_engine.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)

    for name in ("urllib3", "httpx", "httpcore", "googleapiclient", "google", "aiosqlite", "python_multipart"):
        logging.getLogger(name).setLevel(logging.WARNING)


setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Knock Not...")
    await init_db()

    app.state.calendar_store = {}       # calendar_id -> {busy, event, updated_at}
    app.state.discovered_calendars = []  # list of discovered calendar dicts
    app.state.demo_overrides = {}       # room_name_lower -> {state, device_count}

    engine = PresenceEngine(app_state=app.state)
    app.state.engine = engine

    engine_task = asyncio.create_task(engine.run())
    logger.info("Knock Not background loop started")

    yield

    logger.info("Shutting down Knock Not...")
    engine.stop()
    engine_task.cancel()
    try:
        await engine_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Knock Not",
    description="Ghost Booking Detector - Real-time meeting room occupancy via Arista APs",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - start) * 1000
    # Skip static asset noise
    if not request.url.path.startswith(("/style.css", "/app.js", "/uploads/")):
        logger.debug("%s %s %d (%.0fms)", request.method, request.url.path, response.status_code, elapsed)
    return response


app.include_router(floors.router)
app.include_router(rooms.router)
app.include_router(aps.router)
app.include_router(heatmap.router)
app.include_router(health.router)
app.include_router(calendar.router)
app.include_router(demo.router)

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

os.makedirs("dashboard", exist_ok=True)
app.mount("/", StaticFiles(directory="dashboard", html=True), name="dashboard")
