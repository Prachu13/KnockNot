"""
RSSI-to-Distance conversion using the Log-Distance Path-Loss Model.

Formula: distance = 10 ^ ((RSSI_ref - RSSI_measured) / (10 * n))

Where:
- RSSI_ref: RSSI at 1 meter reference distance (calibrated per-AP, typically -42 dBm)
- n: path-loss exponent (2.0 free space, 2.5-4.0 indoors)
"""

from __future__ import annotations

import math


def rssi_to_distance(rssi: int, rssi_ref: float = -42.0, path_loss_exp: float = 3.0) -> float:
    """
    Convert RSSI (dBm) to estimated distance (meters).

    Args:
        rssi: Measured RSSI in dBm (e.g., -55)
        rssi_ref: RSSI at 1 meter reference distance (per-AP calibration)
        path_loss_exp: Path-loss exponent (2.0=free space, 3.0=typical indoor, 4.0=dense walls)

    Returns:
        Estimated distance in meters
    """
    if rssi >= rssi_ref:
        return 0.1  # Very close, clamp to minimum

    exponent = (rssi_ref - rssi) / (10.0 * path_loss_exp)
    distance = math.pow(10, exponent)

    return round(distance, 2)


def distance_to_weight(distance: float) -> float:
    """
    Convert distance to inverse-square weight for trilateration.
    Closer APs get much higher weight.
    """
    if distance <= 0.1:
        distance = 0.1
    return 1.0 / (distance * distance)
