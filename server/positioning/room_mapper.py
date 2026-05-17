"""
Point-in-Polygon room assignment.

Uses ray-casting algorithm to determine which room (defined as a polygon)
a computed device position falls inside.
"""

from __future__ import annotations


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    """
    Ray-casting algorithm for point-in-polygon test.

    Args:
        x, y: Point coordinates
        polygon: List of [x, y] vertices defining the room boundary

    Returns:
        True if point is inside the polygon
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def assign_device_to_room(
    x: float, y: float, rooms: list[dict]
) -> int | None:
    """
    Determine which room a device position falls inside.

    Args:
        x, y: Device position in meters (floor coordinates)
        rooms: List of room dicts with 'id' and 'polygon' keys

    Returns:
        room_id if inside a room, None if in hallway/outside all rooms
    """
    for room in rooms:
        polygon = room.get("polygon", [])
        if polygon and point_in_polygon(x, y, polygon):
            return room["id"]
    return None
