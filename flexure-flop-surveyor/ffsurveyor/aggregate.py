"""Aggregation and scores in the gravity frame (PRD 6.6).

Given per-frame :class:`~ffsurveyor.frame.FrameResult` objects (already rotated
into the gravity frame), compute:

* per burst/approach: mean elongation vector + repeatability (within-burst
  scatter, the seeing/wind discriminant);
* per node: the path-independent **flexure** component (common to approaches)
  and the path-dependent **hysteresis** component (their difference).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Sequence

import numpy as np

from .angles import DoubleAngleVector
from .frame import FrameResult

__all__ = ["BurstAggregate", "NodeAggregate", "aggregate_bursts", "aggregate_nodes"]


# Vector accessors select which frame to aggregate in (PRD point 9).
def _grav(r: FrameResult) -> DoubleAngleVector:
    return r.gravity_vector


def _sensor(r: FrameResult) -> DoubleAngleVector:
    return r.sensor_vector


def _mean_vector(results: Sequence[FrameResult], accessor=_grav) -> DoubleAngleVector:
    xs = np.array([accessor(r).x for r in results], float)
    ys = np.array([accessor(r).y for r in results], float)
    return DoubleAngleVector(np.mean(xs), np.mean(ys))


def _scatter(results: Sequence[FrameResult], mean: DoubleAngleVector, accessor=_grav) -> float:
    """RMS distance of per-frame vectors from the mean, in double-angle space."""
    if len(results) < 2:
        return float("nan")
    d = [
        np.hypot(accessor(r).x - mean.x, accessor(r).y - mean.y)
        for r in results
    ]
    return float(np.sqrt(np.mean(np.square(d))))


@dataclass
class BurstAggregate:
    node_id: str
    approach: str
    burst: str
    mean_vector: DoubleAngleVector
    magnitude: float
    pa_deg: float
    repeatability: float           # within-burst scatter; low => deterministic
    n_frames: int
    alt_deg: Optional[float]
    az_deg: Optional[float]
    outlier_flags: List[bool] = field(default_factory=list)


@dataclass
class NodeAggregate:
    node_id: str
    alt_deg: Optional[float]
    az_deg: Optional[float]
    # Path-independent flexure component (common to approaches).
    flexure_vector: DoubleAngleVector
    flexure_mag: float
    flexure_pa_deg: float
    # Path-dependent hysteresis component (approach difference).
    hysteresis_mag: float
    hysteresis_vector: Optional[DoubleAngleVector]
    approaches: List[str]
    mean_repeatability: float
    n_frames: int
    # Sensor-frame counterparts (PRD point 9): a pattern coherent here but not in
    # the gravity frame indicates a sensor-fixed error (tilt, pinched optic).
    sensor_flexure_mag: float = float("nan")
    sensor_flexure_pa_deg: float = float("nan")
    sensor_hysteresis_mag: float = float("nan")


def _flag_outliers(results: Sequence[FrameResult], mean: DoubleAngleVector, k: float = 4.0):
    """Flag gross outlier frames (candidate wind gusts, PRD 6.6)."""
    if len(results) < 3:
        return [False] * len(results)
    d = np.array(
        [np.hypot(r.gravity_vector.x - mean.x, r.gravity_vector.y - mean.y) for r in results]
    )
    med = np.median(d)
    mad = np.median(np.abs(d - med)) * 1.4826
    if mad <= 0:
        return [False] * len(results)
    return list(d > med + k * mad)


def aggregate_bursts(results: Sequence[FrameResult]) -> List[BurstAggregate]:
    """Group frames by (node, approach, burst) and aggregate each group."""
    groups: Dict[tuple, List[FrameResult]] = defaultdict(list)
    for r in results:
        groups[(r.node_id, r.approach, r.burst)].append(r)

    out: List[BurstAggregate] = []
    for (node_id, approach, burst), grp in sorted(groups.items()):
        mean = _mean_vector(grp)
        alt = np.nanmean([r.alt_deg for r in grp if r.alt_deg is not None]) if any(
            r.alt_deg is not None for r in grp
        ) else None
        az = _circular_mean_az([r.az_deg for r in grp if r.az_deg is not None])
        out.append(
            BurstAggregate(
                node_id=node_id,
                approach=approach,
                burst=burst,
                mean_vector=mean,
                magnitude=float(mean.magnitude),
                pa_deg=float(mean.pa_deg),
                repeatability=_scatter(grp, mean),
                n_frames=len(grp),
                alt_deg=None if alt is None else float(alt),
                az_deg=az,
                outlier_flags=_flag_outliers(grp, mean),
            )
        )
    return out


def _circular_mean_az(azs) -> Optional[float]:
    azs = [a for a in azs if a is not None]
    if not azs:
        return None
    ang = np.radians(azs)
    return float(np.degrees(np.arctan2(np.mean(np.sin(ang)), np.mean(np.cos(ang)))) % 360.0)


def aggregate_nodes(
    results: Sequence[FrameResult],
    burst_filter: Optional[str] = None,
) -> List[NodeAggregate]:
    """Aggregate frames per node, combining approaches (PRD 6.6).

    Parameters
    ----------
    results : sequence of FrameResult
    burst_filter : str, optional
        If given, only frames with this burst tag are used (e.g. "post_settle").
    """
    if burst_filter is not None:
        results = [r for r in results if r.burst == burst_filter]

    by_node: Dict[str, List[FrameResult]] = defaultdict(list)
    for r in results:
        by_node[r.node_id].append(r)

    out: List[NodeAggregate] = []
    for node_id, node_frames in sorted(by_node.items()):
        by_appr: Dict[str, List[FrameResult]] = defaultdict(list)
        for r in node_frames:
            by_appr[r.approach].append(r)

        approaches = sorted(by_appr)

        # Flexure = mean of approach means; hysteresis = their difference.
        # Computed in BOTH frames (PRD point 9).
        flexure, hyst_mag, hyst_vec = _flexure_hysteresis(by_appr, approaches, _grav)
        s_flexure, s_hyst_mag, _ = _flexure_hysteresis(by_appr, approaches, _sensor)

        reps = []
        for fr in by_appr.values():
            m = _mean_vector(fr)
            s = _scatter(fr, m)
            if not np.isnan(s):
                reps.append(s)
        mean_rep = float(np.mean(reps)) if reps else float("nan")

        alt = np.nanmean([r.alt_deg for r in node_frames if r.alt_deg is not None]) if any(
            r.alt_deg is not None for r in node_frames
        ) else None
        az = _circular_mean_az([r.az_deg for r in node_frames])

        out.append(
            NodeAggregate(
                node_id=node_id,
                alt_deg=None if alt is None else float(alt),
                az_deg=az,
                flexure_vector=flexure,
                flexure_mag=float(flexure.magnitude),
                flexure_pa_deg=float(flexure.pa_deg),
                hysteresis_mag=hyst_mag,
                hysteresis_vector=hyst_vec,
                approaches=approaches,
                mean_repeatability=mean_rep,
                n_frames=len(node_frames),
                sensor_flexure_mag=float(s_flexure.magnitude),
                sensor_flexure_pa_deg=float(s_flexure.pa_deg),
                sensor_hysteresis_mag=s_hyst_mag,
            )
        )
    return out


def _flexure_hysteresis(by_appr, approaches, accessor):
    """Flexure (mean of approach means) and hysteresis (their difference)."""
    appr_means = {a: _mean_vector(fr, accessor) for a, fr in by_appr.items()}
    fx = np.mean([appr_means[a].x for a in approaches])
    fy = np.mean([appr_means[a].y for a in approaches])
    flexure = DoubleAngleVector(fx, fy)

    hyst_vec = None
    if len(approaches) >= 2:
        pair_dists = []
        for a, b in combinations(approaches, 2):
            va, vb = appr_means[a], appr_means[b]
            pair_dists.append(np.hypot(va.x - vb.x, va.y - vb.y))
        hyst_mag = float(np.mean(pair_dists))
        if len(approaches) == 2:
            a, b = approaches
            hyst_vec = DoubleAngleVector(
                appr_means[a].x - appr_means[b].x,
                appr_means[a].y - appr_means[b].y,
            )
    else:
        hyst_mag = float("nan")  # cannot separate flop without >=2 approaches
    return flexure, hyst_mag, hyst_vec
