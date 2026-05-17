"""
Arista AP Challenge-Response OTP Authentication.

Flow:
1. SSH to root@<AP_IP> → AP responds with "Response[<CHALLENGE>]:"
2. POST challenge to Arista license server → get OTP signature
3. Use signature as SSH password to authenticate

Mirrors the logic in the user's ap_script.sh.
"""

from __future__ import annotations

import os
import re
import logging
import httpx
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ARISTA_OTP_URL = "https://license.aristanetworks.com/sign/wifi_otp/?format=api"

# In-memory cache: challenge → (response, expiry_time)
_cache: dict[str, tuple[str, datetime]] = {}
CACHE_TTL_MINUTES = 30


def _get_credentials() -> tuple[str, str]:
    username = os.getenv("ARISTA_USERNAME", "")
    password = os.getenv("ARISTA_PASSWORD", "")
    if not username or not password:
        raise ValueError("ARISTA_USERNAME and ARISTA_PASSWORD must be set in environment")
    return username, password


async def get_otp_response(challenge: str) -> str | None:
    """
    Given a challenge string from the AP, get the OTP response
    from Arista's license server.

    Returns the signature string, or None if the request failed.
    """
    # Check cache first
    if challenge in _cache:
        response, expiry = _cache[challenge]
        if datetime.utcnow() < expiry:
            logger.debug("Cache hit for challenge: %s", challenge[:20])
            return response

    username, password = _get_credentials()

    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            resp = await client.post(
                ARISTA_OTP_URL,
                auth=(username, password),
                headers={"X-CSRFTOKEN": ""},
                data={"message": challenge},
            )
            resp.raise_for_status()

            # Parse response - extract signature from HTML-encoded JSON
            # Format: signature&quot;: &quot;<SIGNATURE>&quot;
            text = resp.text
            match = re.search(r'signature&quot;: &quot;([^&]+)', text)
            if not match:
                # Try plain JSON format
                match = re.search(r'"signature"\s*:\s*"([^"]+)"', text)

            if match:
                signature = match.group(1)
                _cache[challenge] = (signature, datetime.utcnow() + timedelta(minutes=CACHE_TTL_MINUTES))
                logger.info("OTP response obtained for challenge: %s...", challenge[:20])
                return signature

            logger.error("Could not parse OTP response: %s", text[:200])
            return None

    except httpx.HTTPError as e:
        logger.error("Failed to get OTP response: %s", e)
        return None


def clear_cache():
    """Clear the OTP response cache."""
    _cache.clear()
