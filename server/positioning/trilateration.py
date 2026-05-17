"""
Multilateration: turn N AP observations + RSSI distances into a single (x, y) position.

Algorithm pipeline (best to worst):
  1. Linear LSQ (initial guess) — fast, closed-form, always finite
  2. Outlier rejection — drop observations with residual > 2x median (multipath/reflection)
  3. Gauss-Newton iterative refinement — minimizes the actual nonlinear residuals
  4. Final weighted by signal quality (1/sigma²) where sigma grows with distance

Why this is more accurate than weighted centroid or pure linear LSQ:
  - LSQ on linearized equations introduces bias when distances are very unequal
  - Gauss-Newton on the nonlinear least-squares cost converges to the true MLE
  - Outlier rejection handles the typical case of one wildly-off AP (door slam,
    body absorption, reflection off a wall)
  - Stronger signals are more reliable — should drive the position more
"""

from __future__ import annotations

import math

from .rssi import rssi_to_distance, distance_to_weight


# Tunable parameters
MAX_GN_ITERATIONS = 8
GN_CONVERGENCE_M = 0.05  # stop iterating when step is < 5 cm
OUTLIER_RESIDUAL_RATIO = 2.5  # drop obs with residual > 2.5x median residual
MIN_OBS_FOR_OUTLIER_REJECTION = 4  # need at least 4 to safely drop one


def weighted_centroid(observations: list[dict]) -> tuple[float, float, float]:
    """
    Main entry point. Dispatches based on AP count:
      - 0 APs: origin
      - 1 AP:  pin to AP location
      - 2 APs: weighted centroid (can't trilaterate with 2 points uniquely)
      - 3+ APs: full multilateration pipeline (LSQ + outlier rejection + GN refinement)
    """
    if not observations:
        return (0.0, 0.0, 0.0)

    if len(observations) == 1:
        obs = observations[0]
        return (obs["x"], obs["y"], 0.3)

    # Convert RSSI → distance once
    enriched = []
    for obs in observations:
        if "distance" in obs and obs["distance"] is not None:
            d = obs["distance"]
        else:
            d = rssi_to_distance(
                obs["rssi"],
                obs.get("rssi_ref", -42.0),
                obs.get("path_loss_exp", 3.0),
            )
        enriched.append({
            "x": obs["x"], "y": obs["y"],
            "distance": max(d, 0.5),  # avoid div-by-zero
            "rssi": obs["rssi"],
        })

    if len(enriched) >= 3:
        result = _multilaterate(enriched)
        if result is not None:
            x, y = result
            return (round(x, 2), round(y, 2), _confidence(observations))

    # Fallback for 2-AP case
    return _weighted_centroid_fallback(observations, enriched)


def _multilaterate(enriched: list[dict]) -> tuple[float, float] | None:
    """
    Full multilateration pipeline:
      1. RANSAC-style outlier rejection (when 4+ APs)
      2. Linear LSQ initial guess from inliers
      3. Gauss-Newton refinement on inliers
    """
    # Step 1: outlier rejection via RANSAC for 4+ observations
    if len(enriched) >= MIN_OBS_FOR_OUTLIER_REJECTION:
        enriched = _ransac_inliers(enriched)

    # Step 2: linear LSQ initial guess
    initial = _linear_lsq(enriched)
    if initial is None:
        return None

    # Step 3: Gauss-Newton refinement
    refined = _gauss_newton_refine(enriched, initial)
    return refined if refined is not None else initial


def _ransac_inliers(enriched: list[dict]) -> list[dict]:
    """
    RANSAC-style outlier filtering: for each subset of 3 APs, compute
    a candidate position and count how many other APs agree (residual < 1.5m).
    Keep the subset with most agreement.
    """
    n = len(enriched)
    best_inliers = enriched
    best_score = 0
    INLIER_TOLERANCE_M = 1.5

    # Try all 3-AP subsets (up to ~20 combinations for 6 APs — fast enough)
    from itertools import combinations
    for indices in combinations(range(n), 3):
        subset = [enriched[i] for i in indices]
        candidate = _linear_lsq(subset)
        if candidate is None:
            continue
        cx, cy = candidate

        # Count inliers: APs whose measured distance matches computed distance
        inliers = []
        for i, obs in enumerate(enriched):
            computed = math.hypot(obs["x"] - cx, obs["y"] - cy)
            if abs(computed - obs["distance"]) <= INLIER_TOLERANCE_M:
                inliers.append(obs)

        if len(inliers) > best_score:
            best_score = len(inliers)
            best_inliers = inliers

    # If we found a strong consensus (5+ inliers), use them; otherwise use all
    return best_inliers if best_score >= 3 else enriched


def _linear_lsq(enriched: list[dict]) -> tuple[float, float] | None:
    """
    Linearized least-squares: subtract first equation from each other to get
    a linear system Ap = b. Solve via normal equations.
    """
    n = len(enriched)
    if n < 3:
        return None

    x1, y1, d1 = enriched[0]["x"], enriched[0]["y"], enriched[0]["distance"]

    A = []
    b = []
    for obs in enriched[1:]:
        xi, yi, di = obs["x"], obs["y"], obs["distance"]
        A.append([2 * (xi - x1), 2 * (yi - y1)])
        b.append(xi*xi - x1*x1 + yi*yi - y1*y1 + d1*d1 - di*di)

    AtA00 = AtA01 = AtA11 = 0.0
    Atb0 = Atb1 = 0.0
    for i in range(len(A)):
        AtA00 += A[i][0] * A[i][0]
        AtA01 += A[i][0] * A[i][1]
        AtA11 += A[i][1] * A[i][1]
        Atb0 += A[i][0] * b[i]
        Atb1 += A[i][1] * b[i]

    det = AtA00 * AtA11 - AtA01 * AtA01
    if abs(det) < 1e-9:
        return None  # singular — APs collinear

    x = (AtA11 * Atb0 - AtA01 * Atb1) / det
    y = (-AtA01 * Atb0 + AtA00 * Atb1) / det
    return (x, y)


def _gauss_newton_refine(
    enriched: list[dict], initial: tuple[float, float]
) -> tuple[float, float] | None:
    """
    Iteratively refine the position by minimizing weighted nonlinear residuals.
    Each iteration:
      1. Compute residuals r_i = computed_dist(p) - measured_dist
      2. Compute Jacobian J (∂r/∂p for each observation)
      3. Solve normal equations (JᵀWJ) Δp = -JᵀWr  for step Δp
      4. Update p ← p + Δp; stop when |Δp| < tolerance

    Weights W = 1/σ² where σ grows with distance (closer = more reliable).
    """
    x, y = initial

    for _ in range(MAX_GN_ITERATIONS):
        # Build Jacobian and residual vector
        JtJ00 = JtJ01 = JtJ11 = 0.0
        Jtr0 = Jtr1 = 0.0

        for obs in enriched:
            dx = x - obs["x"]
            dy = y - obs["y"]
            computed = math.hypot(dx, dy)
            if computed < 1e-6:
                continue  # avoid div by zero

            residual = computed - obs["distance"]

            # Jacobian row: [dx/d, dy/d]
            jx = dx / computed
            jy = dy / computed

            # Weight: stronger signal = more reliable
            # σ = 0.5 * distance (rough approximation of RSSI distance error)
            # weight = 1 / σ²
            sigma = 0.5 * obs["distance"] + 1.0  # +1 to avoid div by zero
            w = 1.0 / (sigma * sigma)

            JtJ00 += w * jx * jx
            JtJ01 += w * jx * jy
            JtJ11 += w * jy * jy
            Jtr0 += w * jx * residual
            Jtr1 += w * jy * residual

        # Solve 2x2: JtJ * dp = -Jtr
        det = JtJ00 * JtJ11 - JtJ01 * JtJ01
        if abs(det) < 1e-9:
            break  # singular — bail out

        dx_step = (-JtJ11 * Jtr0 + JtJ01 * Jtr1) / det
        dy_step = (JtJ01 * Jtr0 - JtJ00 * Jtr1) / det

        x += dx_step
        y += dy_step

        if math.hypot(dx_step, dy_step) < GN_CONVERGENCE_M:
            break

    return (x, y)


def _weighted_centroid_fallback(
    observations: list[dict], enriched: list[dict]
) -> tuple[float, float, float]:
    """Weighted centroid for 2-AP case or when LSQ is singular."""
    total_weight = 0.0
    weighted_x = 0.0
    weighted_y = 0.0

    for obs in enriched:
        weight = distance_to_weight(obs["distance"])
        weighted_x += weight * obs["x"]
        weighted_y += weight * obs["y"]
        total_weight += weight

    if total_weight == 0:
        return (0.0, 0.0, 0.0)

    x = weighted_x / total_weight
    y = weighted_y / total_weight
    return (round(x, 2), round(y, 2), _confidence(observations))


def _confidence(observations: list[dict]) -> float:
    """Estimate position confidence (0.0–1.0) based on AP count and avg RSSI."""
    n_aps = len(observations)
    avg_rssi = sum(o["rssi"] for o in observations) / n_aps
    ap_factor = min(n_aps / 4.0, 1.0)  # saturates at 4 APs
    rssi_factor = max(0.0, min(1.0, (avg_rssi + 90) / 40.0))
    return round(ap_factor * 0.6 + rssi_factor * 0.4, 2)
