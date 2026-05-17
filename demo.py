#!/usr/bin/env python3
"""
Demo control script for Knock Not presentations.

USAGE:
  python demo.py raigad 32              # 32 people in Raigad → OCCUPIED
  python demo.py vishalgad 0            # Force Vishalgad VACANT (clear false positive)
  python demo.py ghost janjira          # Force Janjira to GHOST_BOOKING
  python demo.py preset present         # Quick preset: full demo scenario
  python demo.py list                   # Show active overrides
  python demo.py reset                  # Clear all overrides → back to live data

You can pass multiple rooms in one call:
  python demo.py raigad 32 vishalgad 0 visapur 0 pratapgad 0 sinhgad 0
"""

import sys
import json
import urllib.request
import urllib.error

SERVER = "http://localhost:8000"


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{SERVER}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode('utf-8', errors='replace')}")
        sys.exit(1)
    except Exception as e:
        print(f"Connection error: {e}")
        sys.exit(1)


def get(path):
    try:
        with urllib.request.urlopen(f"{SERVER}{path}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_set(args):
    """Pairs of (room_name, count) → set overrides."""
    if len(args) % 2 != 0:
        print("Error: arguments must be pairs of room_name and count")
        sys.exit(1)

    overrides = []
    for i in range(0, len(args), 2):
        name = args[i]
        try:
            count = int(args[i + 1])
        except ValueError:
            print(f"Error: count must be a number, got '{args[i + 1]}'")
            sys.exit(1)
        overrides.append({"room_name": name, "device_count": count})

    result = post("/api/v1/demo/override", {"overrides": overrides})
    print(f"OK — {result['active']} overrides active:")
    for room, ov in result["overrides"].items():
        print(f"  {room}: state={ov['state']} devices={ov['device_count']}")


def cmd_ghost(room_name):
    """Force a room to GHOST_BOOKING (booked but empty)."""
    result = post("/api/v1/demo/override", {
        "overrides": [{"room_name": room_name, "device_count": 0, "state": "GHOST_BOOKING"}]
    })
    print(f"OK — {room_name} forced to GHOST_BOOKING")


def cmd_adhoc(room_name, count):
    """Force a room to ADHOC_USE (occupied but no booking)."""
    result = post("/api/v1/demo/override", {
        "overrides": [{"room_name": room_name, "device_count": count, "state": "ADHOC_USE"}]
    })
    print(f"OK — {room_name} forced to ADHOC_USE with {count} devices")


def cmd_preset(name):
    """Predefined demo scenarios."""
    presets = {
        "present": {
            # Raigad: where you're presenting (full)
            # Janjira: ghost booking (booked but no one)
            # Sinhgad: ad-hoc meeting (occupied but no booking)
            # Others: empty
            "raigad": {"device_count": 32, "state": "OCCUPIED"},
            "janjira": {"device_count": 0, "state": "GHOST_BOOKING"},
            "sinhgad": {"device_count": 8, "state": "ADHOC_USE"},
            "vishalgad": {"device_count": 0, "state": "VACANT"},
            "visapur": {"device_count": 0, "state": "VACANT"},
            "pratapgad": {"device_count": 0, "state": "VACANT"},
            "torna": {"device_count": 0, "state": "VACANT"},
            "panhala": {"device_count": 0, "state": "VACANT"},
            "lohgad": {"device_count": 0, "state": "VACANT"},
            "shivneri": {"device_count": 0, "state": "VACANT"},
        },
        "everyone-vacant": {
            r: {"device_count": 0, "state": "VACANT"} for r in [
                "raigad", "janjira", "sinhgad", "vishalgad", "visapur",
                "pratapgad", "torna", "panhala", "lohgad", "shivneri",
            ]
        },
    }

    if name not in presets:
        print(f"Unknown preset: {name}")
        print(f"Available: {', '.join(presets.keys())}")
        sys.exit(1)

    overrides = [
        {"room_name": room, "device_count": cfg["device_count"], "state": cfg["state"]}
        for room, cfg in presets[name].items()
    ]
    result = post("/api/v1/demo/override", {"overrides": overrides})
    print(f"OK — preset '{name}' applied. {result['active']} rooms overridden:")
    for room, ov in sorted(result["overrides"].items()):
        marker = "●" if ov["device_count"] > 0 else "○"
        print(f"  {marker} {room:14s} {ov['state']:15s} ({ov['device_count']} devices)")


def cmd_list():
    """Show active overrides."""
    result = get("/api/v1/demo/override")
    if result["count"] == 0:
        print("No active overrides — showing live data.")
        return
    print(f"{result['count']} active overrides:")
    for room, ov in sorted(result["overrides"].items()):
        marker = "●" if ov["device_count"] > 0 else "○"
        print(f"  {marker} {room:14s} {ov['state']:15s} ({ov['device_count']} devices)")


def cmd_reset():
    """Clear all overrides."""
    result = post("/api/v1/demo/clear", {})
    print(f"OK — cleared {result['removed']} overrides. Back to live data.")


def usage():
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        usage()

    cmd = args[0].lower()

    if cmd == "list":
        cmd_list()
    elif cmd == "reset" or cmd == "clear":
        cmd_reset()
    elif cmd == "ghost":
        if len(args) != 2:
            print("Usage: python demo.py ghost <room_name>")
            sys.exit(1)
        cmd_ghost(args[1])
    elif cmd == "adhoc":
        if len(args) != 3:
            print("Usage: python demo.py adhoc <room_name> <count>")
            sys.exit(1)
        cmd_adhoc(args[1], int(args[2]))
    elif cmd == "preset":
        if len(args) != 2:
            print("Usage: python demo.py preset <name>")
            sys.exit(1)
        cmd_preset(args[1])
    else:
        # Default: treat all args as (room_name, count) pairs
        cmd_set(args)
