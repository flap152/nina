"""Run-pair comparisons in the common gravity frame (PRD 6.6).

All comparisons require both runs to already be normalized into the gravity
frame (which the per-frame pipeline guarantees via the mandatory sensor-angle
transform, PRD 6.4a). Here we simply difference matched nodes.

Supported diffs:
  * Guidescope - OAG  (headline: how much flexure common-path guiding removes)
  * Locks on - Locks off
  * Placement A - Placement B
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .angles import DoubleAngleVector
from .aggregate import NodeAggregate

__all__ = ["NodeComparison", "RunComparison", "compare_runs"]


@dataclass
class NodeComparison:
    node_id: str
    alt_deg: Optional[float]
    az_deg: Optional[float]
    flexure_a: float
    flexure_b: float
    flexure_diff_vector: DoubleAngleVector   # A - B (gravity frame)
    flexure_diff_mag: float                  # |A - B|
    flexure_reduction: float                 # |A| - |B| (positive => B smaller)
    hysteresis_a: float
    hysteresis_b: float
    hysteresis_reduction: float              # A - B


@dataclass
class RunComparison:
    label_a: str
    label_b: str
    nodes: List[NodeComparison]
    mean_flexure_a: float
    mean_flexure_b: float
    mean_flexure_reduction: float            # mean(|A|) - mean(|B|)
    mean_hysteresis_reduction: float

    def summary(self) -> str:
        return (
            f"{self.label_a} vs {self.label_b}: "
            f"mean flexure {self.mean_flexure_a:.3f} -> {self.mean_flexure_b:.3f} "
            f"(reduction {self.mean_flexure_reduction:+.3f}); "
            f"mean hysteresis reduction {self.mean_hysteresis_reduction:+.3f} "
            f"over {len(self.nodes)} matched node(s)."
        )


def compare_runs(
    run_a: List[NodeAggregate],
    run_b: List[NodeAggregate],
    label_a: str = "A",
    label_b: str = "B",
) -> RunComparison:
    """Difference two per-node aggregations, matched by node ID."""
    a_by: Dict[str, NodeAggregate] = {n.node_id: n for n in run_a}
    b_by: Dict[str, NodeAggregate] = {n.node_id: n for n in run_b}
    common = sorted(set(a_by) & set(b_by))

    nodes: List[NodeComparison] = []
    for nid in common:
        na, nb = a_by[nid], b_by[nid]
        dvec = DoubleAngleVector(
            na.flexure_vector.x - nb.flexure_vector.x,
            na.flexure_vector.y - nb.flexure_vector.y,
        )
        hyst_red = _sub_nan(na.hysteresis_mag, nb.hysteresis_mag)
        nodes.append(
            NodeComparison(
                node_id=nid,
                alt_deg=na.alt_deg if na.alt_deg is not None else nb.alt_deg,
                az_deg=na.az_deg if na.az_deg is not None else nb.az_deg,
                flexure_a=na.flexure_mag,
                flexure_b=nb.flexure_mag,
                flexure_diff_vector=dvec,
                flexure_diff_mag=float(dvec.magnitude),
                flexure_reduction=na.flexure_mag - nb.flexure_mag,
                hysteresis_a=na.hysteresis_mag,
                hysteresis_b=nb.hysteresis_mag,
                hysteresis_reduction=hyst_red,
            )
        )

    if nodes:
        mean_a = float(np.mean([n.flexure_a for n in nodes]))
        mean_b = float(np.mean([n.flexure_b for n in nodes]))
        hyst_reds = [n.hysteresis_reduction for n in nodes if not np.isnan(n.hysteresis_reduction)]
        mean_hyst = float(np.mean(hyst_reds)) if hyst_reds else float("nan")
    else:
        mean_a = mean_b = mean_hyst = float("nan")

    return RunComparison(
        label_a=label_a,
        label_b=label_b,
        nodes=nodes,
        mean_flexure_a=mean_a,
        mean_flexure_b=mean_b,
        mean_flexure_reduction=mean_a - mean_b,
        mean_hysteresis_reduction=mean_hyst,
    )


def _sub_nan(a: float, b: float) -> float:
    if np.isnan(a) or np.isnan(b):
        return float("nan")
    return a - b
