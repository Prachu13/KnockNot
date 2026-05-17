"""
SSH Poller for Arista APs.

Uses pexpect to handle Arista's challenge-response OTP SSH authentication.
Once logged in, runs `client 127.0.0.1 ALL` which uses the AP's SCANNING RADIO
to detect ALL nearby devices (associated AND unassociated) with RSSI/SNR.

This is critical for triangulation: each AP reports what it sees, and the
server computes positions by combining RSSI from multiple APs that see the
same MAC.

Auth flow:
1. SSH root@<ip> → AP responds with "Response[<challenge>]:"
2. POST challenge to Arista license server → get OTP signature
3. Send signature as SSH password → root shell
4. Run `hostname` and `client 127.0.0.1 ALL`
5. Parse the column-formatted output → build APReport
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import pexpect

from .arista_auth import get_otp_response
from .models import APReport, ClientReport

logger = logging.getLogger(__name__)

MARKER = "---PRESENCE_MARKER---"
# We collect EVERYTHING down to this floor, server-side decides what to do.
# Below -85 dBm signal is effectively garbage for positioning.
COLLECT_FLOOR_DBM = -85


async def poll_single_ap(ip: str, ap_db_id: int, timeout: int = 60) -> APReport | None:
    """SSH into a single Arista AP and collect all visible client data."""
    try:
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _poll_sync, ip, ap_db_id),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.error("AP %s (id=%d): overall timeout after %ds", ip, ap_db_id, timeout)
        return None
    except Exception as e:
        logger.error("AP %s (id=%d): unexpected error: %s", ip, ap_db_id, e)
        return None


def _poll_sync(ip: str, ap_db_id: int) -> APReport | None:
    """Synchronous SSH poll — runs in thread pool."""
    child = None
    try:
        # ── Step 1: Connect ──
        logger.info("AP %s: connecting...", ip)
        child = pexpect.spawn(
            f'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{ip}',
            timeout=20,
            encoding='utf-8',
            maxread=131072,  # large for big client tables
        )

        # ── Step 2: Handle challenge ──
        idx = child.expect([
            r'Response\[(.+?)\]:',
            r'[Pp]assword:',
            pexpect.TIMEOUT,
            pexpect.EOF,
        ], timeout=15)

        if idx == 0:
            challenge = child.match.group(1)
            logger.debug("AP %s: got challenge %s...", ip, challenge[:20])
        elif idx == 1:
            logger.error("AP %s: password prompt (root not unlocked?)", ip)
            return None
        else:
            logger.error("AP %s: no challenge prompt (timeout or EOF)", ip)
            return None

        # ── Step 3: Get OTP ──
        loop = asyncio.new_event_loop()
        try:
            otp = loop.run_until_complete(get_otp_response(challenge))
        finally:
            loop.close()

        if not otp:
            logger.error("AP %s: OTP resolution failed", ip)
            return None

        # ── Step 4: Authenticate ──
        child.sendline(otp)
        idx = child.expect([r'[#\$]\s*$', pexpect.TIMEOUT], timeout=10)
        if idx != 0:
            logger.error("AP %s: no shell prompt after OTP", ip)
            return None

        logger.info("AP %s: logged in successfully", ip)

        # ── Step 5: Get hostname ──
        ap_hostname = _clean_hostname(_run_cmd(child, 'hostname', timeout=5)) or ip

        # ── Step 6: Run client scan ──
        # `client 127.0.0.1 client` returns ONLY actual client devices (phones,
        # laptops, IoT) seen by the scanning radio — not neighbor APs.
        # The output format has fractional RSSI (e.g. -48.71) and no dBm suffix.
        scan_output = _run_cmd(child, 'client 127.0.0.1 client', timeout=20)

        if not scan_output:
            logger.warning("AP %s (%s): empty scan output", ip, ap_hostname)
            scan_output = ""

        # ── Step 7: Disconnect ──
        child.sendline('exit')
        try:
            child.expect(pexpect.EOF, timeout=3)
        except Exception:
            pass
        child.close()

        # ── Step 8: Parse output ──
        all_devices = _parse_client_table(scan_output)
        # Collect everything above the floor — engine decides per-device threshold
        usable = [d for d in all_devices if d.rssi > COLLECT_FLOOR_DBM]

        report = APReport(
            ap_id=ap_hostname,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            associated_clients=usable,
        )

        # Bucketize by signal strength for visibility
        very_strong = sum(1 for d in usable if d.rssi > -50)
        strong = sum(1 for d in usable if -50 >= d.rssi > -65)
        medium = sum(1 for d in usable if -65 >= d.rssi > -75)
        weak = sum(1 for d in usable if -75 >= d.rssi > -85)
        logger.info(
            "AP %s (%s): scanned %d total, kept %d (>-85dBm)  | strong<-50:%d, mid:%d, weak<-75:%d, very_strong>-50:%d",
            ip, ap_hostname, len(all_devices), len(usable),
            strong, medium, weak, very_strong,
        )

        return report

    except pexpect.exceptions.ExceptionPexpect as e:
        logger.error("AP %s: pexpect error: %s", ip, e)
        return None
    except Exception as e:
        logger.error("AP %s: error: %s", ip, e, exc_info=True)
        return None
    finally:
        if child and child.isalive():
            child.close(force=True)


def _clean_hostname(raw: str | None) -> str:
    """Extract just the hostname from raw command output (strip prompts/echoes)."""
    if not raw:
        return ""
    for line in raw.splitlines():
        line = line.strip()
        # Skip empty, prompts, echoed commands, marker lines
        if not line or line.startswith(('~', '/', '#', '$')):
            continue
        if 'echo' in line or 'hostname' in line.lower() or MARKER in line:
            continue
        # Remove any trailing prompt junk
        line = re.sub(r'\s*[~/]?\s*[#\$]\s*.*$', '', line).strip()
        if line and line != '(none)':
            return line
    return ""


def _run_cmd(child: pexpect.spawn, cmd: str, timeout: int = 10) -> str | None:
    """
    Run a command on the AP and capture its output.
    Uses an echo marker to reliably detect end of output.
    """
    try:
        # Drain any pending output
        try:
            child.expect([r'[#\$]\s*$', pexpect.TIMEOUT], timeout=1)
        except Exception:
            pass

        child.sendline(cmd)
        child.sendline(f'echo {MARKER}')

        idx = child.expect([MARKER, pexpect.TIMEOUT], timeout=timeout)
        if idx != 0:
            logger.warning("AP cmd timeout: %s", cmd)
            return None

        raw = child.before or ""
        # Wait for prompt after marker
        try:
            child.expect([r'[#\$]\s*$', pexpect.TIMEOUT], timeout=2)
        except Exception:
            pass

        return raw
    except Exception as e:
        logger.warning("AP cmd error (%s): %s", cmd, e)
        return None


# ── Column positions in the `client` table output ──
# Header line tells us where each column starts
# We use the dashes line to reliably detect column widths

_DASHES_LINE = re.compile(r'^-{5,}\s+-+')


_MAC_RE = re.compile(r'\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b')


def _parse_client_table(raw_output: str) -> list[ClientReport]:
    """
    Parse the output of `client 127.0.0.1 client`.

    Format (whitespace-separated, no dashes line):
        Client List:
        SSID  MAC               Band  Chn ... RSSI   SNR   ... Self Wds Brg ToSrv ...
              84:2F:57:43:70:01 5GHz  120 ... -61.98 33.02 ...    0   0   0     0 ...
              34:CF:F6:E0:73:83 5GHz  149 ... -48.71 46.29 ...    0   0   0     0 ...

    Strategy: extract MAC and RSSI per line via regex.
    For each line, find the MAC, then find the first negative decimal in
    the RSSI range (between -1 and -100) appearing AFTER the MAC.
    """
    devices = []
    seen_macs = {}  # mac_lower -> best RSSI

    for line in raw_output.splitlines():
        # Find MAC address anywhere on the line
        mac_match = _MAC_RE.search(line)
        if not mac_match:
            continue

        mac_raw = mac_match.group(1)
        # The RSSI is a negative decimal number that appears AFTER the MAC
        # Look for patterns like -61.98 or -45 (typically 6-7 chars long)
        # Skip integer-only floats like -1 (could be channel offset etc.)
        after_mac = line[mac_match.end():]
        # RSSI is the first decimal number with a fractional part in range [-100, -10]
        # Format: -DD.DD
        rssi_matches = re.findall(r'(-\d{1,2}\.\d{1,2})\b', after_mac)
        if not rssi_matches:
            continue

        # First decimal-with-fraction in valid RSSI range
        rssi_val = None
        for m in rssi_matches:
            v = float(m)
            if -100 <= v <= -10:
                rssi_val = v
                break

        if rssi_val is None:
            continue

        # RSSI of -96.0 means "not seen recently" (noise floor placeholder)
        if rssi_val <= -95:
            continue

        rssi = int(round(rssi_val))
        mac_lower = mac_raw.lower()

        # Deduplicate: keep strongest signal per MAC
        if mac_lower in seen_macs:
            if rssi > seen_macs[mac_lower]:
                seen_macs[mac_lower] = rssi
                # Update existing entry
                for d in devices:
                    if d.mac.lower() == mac_lower:
                        d.rssi = rssi
                        break
            continue

        seen_macs[mac_lower] = rssi
        devices.append(ClientReport(mac=mac_raw, rssi=rssi, connected_time=0))

    return devices


async def poll_all_aps(aps: list[dict], timeout: int = 60) -> dict[int, APReport]:
    """Poll all APs concurrently. Returns dict of ap_db_id → APReport."""
    if not aps:
        return {}

    tasks = [
        poll_single_ap(ap["ip_address"], ap["id"], timeout=timeout)
        for ap in aps
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    reports = {}
    for ap, result in zip(aps, results):
        if isinstance(result, APReport):
            reports[ap["id"]] = result
        elif isinstance(result, Exception):
            logger.error("AP %s: poll exception: %s", ap["ip_address"], result)

    success = len(reports)
    total = len(aps)
    if success > 0:
        # Cross-AP sighting count for triangulation visibility
        all_macs = set()
        macs_per_ap = {}
        for ap_id, report in reports.items():
            macs = {c.mac.lower() for c in report.associated_clients}
            macs_per_ap[ap_id] = macs
            all_macs |= macs
        multi_seen = sum(
            1 for mac in all_macs
            if sum(1 for ap_macs in macs_per_ap.values() if mac in ap_macs) >= 2
        )
        logger.info(
            "AP poll complete: %d/%d successful, %d unique devices, %d seen by 2+ APs (triangulatable)",
            success, total, len(all_macs), multi_seen,
        )
    else:
        logger.warning("AP poll complete: 0/%d — no APs responded", total)

    return reports
