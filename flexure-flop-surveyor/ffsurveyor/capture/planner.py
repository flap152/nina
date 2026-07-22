"""Grid + approach-pair planning (PRD 6.1, 6.2, 6.2b).

Nodes are defined in **alt/az** — the physically-fixed, gravity-relevant frame.
Their RA/Dec drifts with time, so it is resolved at *execution* time by the
runner, not baked in here.

Each node carries an **approach pair**: two legs that differ in the direction of
the last load on the mirror (PRD 6.2). Each leg is executed by the runner as an
**offset-then-slew-in** (PRD 6.2b) so the final-leg direction is a controlled
experimental variable rather than whatever the mount's anti-backlash convention
would impose.

Two load axes are supported:

* ``"altitude"`` — the offset point sits above/below the node in altitude, so the
  final slew-in loads the mirror along the tube axis in opposite senses.
* ``"dec"`` — the offset is applied in declination after the alt/az→RA/Dec
  conversion, i.e. arrive after a large +Dec vs -Dec slew.

Which axis actually exercises *this* rig's flop is an M0 empirical question
(PRD 6.2b, 9); both are provided so M0 can compare them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

__all__ = ["ApproachLeg", "NodePlan", "SurveyPlan", "build_survey_plan"]


@dataclass(frozen=True)
class ApproachLeg:
    """One controlled approach to a node."""

    tag: str                 # e.g. "high" / "low" / "pdec" / "mdec"
    load_axis: str           # "altitude" | "dec"
    offset_sign: int         # +1 / -1: direction of the offset before slewing in
    offset_deg: float        # magnitude of the offset leg

    def __post_init__(self):
        if self.load_axis not in ("altitude", "dec"):
            raise ValueError("load_axis must be 'altitude' or 'dec'")
        if self.offset_sign not in (1, -1):
            raise ValueError("offset_sign must be +1 or -1")


@dataclass
class NodePlan:
    node_id: str
    alt_deg: float
    az_deg: float
    approaches: List[ApproachLeg]


@dataclass
class SurveyPlan:
    nodes: List[NodePlan]
    load_axis: str
    offset_deg: float
    min_altitude_deg: float
    zenith_guard_deg: float
    notes: str = ""

    def __len__(self) -> int:
        return len(self.nodes)


def _default_approach_pair(load_axis: str, offset_deg: float) -> List[ApproachLeg]:
    if load_axis == "altitude":
        return [
            ApproachLeg("high", "altitude", +1, offset_deg),  # come DOWN onto node
            ApproachLeg("low", "altitude", -1, offset_deg),   # come UP onto node
        ]
    return [
        ApproachLeg("pdec", "dec", +1, offset_deg),           # arrive after +Dec slew
        ApproachLeg("mdec", "dec", -1, offset_deg),           # arrive after -Dec slew
    ]


def build_survey_plan(
    alt_bands_deg: Tuple[float, ...] = (30.0, 50.0, 70.0, 80.0),
    azimuths_deg: Tuple[float, ...] = (0.0, 90.0, 180.0, 270.0),
    *,
    load_axis: str = "altitude",
    offset_deg: float = 8.0,
    min_altitude_deg: float = 20.0,
    zenith_guard_deg: float = 85.0,
    extra_nodes: Optional[List[Tuple[str, float, float]]] = None,
) -> SurveyPlan:
    """Build a survey grid with per-node approach pairs (PRD 6.1).

    Parameters
    ----------
    alt_bands_deg, azimuths_deg : tuple of float
        The grid. Default: altitude bands x azimuths.
    load_axis : {"altitude", "dec"}
        Which mirror-load axis the approach pair exercises.
    offset_deg : float
        Magnitude of the offset-then-slew-in leg.
    min_altitude_deg : float
        Skip nodes (and altitude-axis offset points) below this.
    zenith_guard_deg : float
        Skip nodes (and altitude-axis offset points) above this (mount keyhole /
        fast azimuth slew near zenith, PRD 6.1).
    extra_nodes : list of (node_id, alt, az), optional
        Explicit extra nodes (e.g. near-meridian / near-horizon, PRD 6.1).

    Notes
    -----
    For the ``"altitude"`` axis, an approach is dropped if its offset point would
    fall outside ``[min_altitude_deg, zenith_guard_deg]`` — feasibility there is
    time-independent because altitude is fixed. ``"dec"``-axis offset feasibility
    depends on time and is checked by the runner at execution.
    """
    nodes: List[NodePlan] = []

    def node_id(alt, az):
        return f"Alt{int(round(alt)):02d}-Az{int(round(az)):03d}"

    candidates: List[Tuple[str, float, float]] = []
    for alt in alt_bands_deg:
        for az in azimuths_deg:
            candidates.append((node_id(alt, az), float(alt), float(az)))
    if extra_nodes:
        candidates.extend((nid, float(a), float(z)) for nid, a, z in extra_nodes)

    for nid, alt, az in candidates:
        if alt < min_altitude_deg or alt > zenith_guard_deg:
            continue
        pair = _default_approach_pair(load_axis, offset_deg)
        if load_axis == "altitude":
            feasible = []
            for leg in pair:
                off_alt = alt + leg.offset_sign * leg.offset_deg
                if min_altitude_deg <= off_alt <= zenith_guard_deg:
                    feasible.append(leg)
            if len(feasible) < 2:
                # Can't form an opposite-sense pair here (e.g. too near horizon);
                # skip rather than silently measure a degenerate single approach.
                continue
            pair = feasible
        nodes.append(NodePlan(node_id=nid, alt_deg=alt, az_deg=az, approaches=pair))

    notes = (
        f"{len(nodes)} node(s); load_axis={load_axis}; offset={offset_deg} deg; "
        f"altitude-axis offsets kept within [{min_altitude_deg}, {zenith_guard_deg}]."
    )
    return SurveyPlan(
        nodes=nodes,
        load_axis=load_axis,
        offset_deg=offset_deg,
        min_altitude_deg=min_altitude_deg,
        zenith_guard_deg=zenith_guard_deg,
        notes=notes,
    )
