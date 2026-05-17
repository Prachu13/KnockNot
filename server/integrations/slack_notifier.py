"""
Slack Webhook notifications using Block Kit.

Sends rich notifications for:
- Ghost booking detected (room booked but empty)
- Ad-hoc meeting detected (room occupied but not booked)
- Ghost resolved (people showed up after ghost alert)
"""

from __future__ import annotations

import os
import logging
from datetime import datetime

import httpx

from server.detection.models import OccupancyEvent, EventType

logger = logging.getLogger(__name__)


def _get_webhook_url() -> str | None:
    url = os.getenv("SLACK_WEBHOOK_URL", "")
    return url if url else None


async def send_notification(event: OccupancyEvent):
    """Send a Slack notification for an occupancy event."""
    webhook_url = _get_webhook_url()
    if not webhook_url:
        logger.warning("Slack webhook URL not configured, skipping notification")
        return

    payload = _build_payload(event)
    if not payload:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 200:
                logger.info("Slack notification sent: %s for %s", event.type.value, event.room_name)
            else:
                logger.error("Slack webhook failed (%d): %s", resp.status_code, resp.text)
    except httpx.HTTPError as e:
        logger.error("Slack notification error: %s", e)


def _build_payload(event: OccupancyEvent) -> dict | None:
    handlers = {
        EventType.GHOST_DETECTED: _ghost_detected_payload,
        EventType.GHOST_RESOLVED: _ghost_resolved_payload,
        EventType.ADHOC_DETECTED: _adhoc_detected_payload,
        EventType.ADHOC_ENDED: _adhoc_ended_payload,
    }
    handler = handlers.get(event.type)
    return handler(event) if handler else None


def _ghost_detected_payload(event: OccupancyEvent) -> dict:
    details = event.details
    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Ghost Booking Detected",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Room:*\n{event.room_name}"},
                    {"type": "mrkdwn", "text": f"*Floor:*\n{event.floor_name}"},
                    {"type": "mrkdwn", "text": f"*Meeting:*\n{details.get('event_title', 'Unknown')}"},
                    {"type": "mrkdwn", "text": f"*Organizer:*\n{details.get('organizer', 'Unknown')}"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Room has been empty for {details.get('empty_minutes', '10+')} minutes. "
                                f"Booking auto-released at {datetime.utcnow().strftime('%H:%M UTC')}.",
                    }
                ],
            },
        ]
    }


def _ghost_resolved_payload(event: OccupancyEvent) -> dict:
    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Ghost Alert Resolved",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Someone just showed up in *{event.room_name}* ({event.floor_name}). "
                            f"Ghost alert cancelled.",
                },
            },
        ]
    }


def _adhoc_detected_payload(event: OccupancyEvent) -> dict:
    device_count = event.details.get("device_count", "multiple")
    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Ad-hoc Meeting Detected",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Room:*\n{event.room_name}"},
                    {"type": "mrkdwn", "text": f"*Floor:*\n{event.floor_name}"},
                    {"type": "mrkdwn", "text": f"*Devices:*\n{device_count} detected"},
                    {"type": "mrkdwn", "text": "*Status:*\nNot booked on calendar"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "An unbooked meeting has been in progress for 5+ minutes.",
                    }
                ],
            },
        ]
    }


def _adhoc_ended_payload(event: OccupancyEvent) -> dict:
    return {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Ad-hoc meeting in *{event.room_name}* ({event.floor_name}) has ended. "
                            f"Room is now available.",
                },
            },
        ]
    }
