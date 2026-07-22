"""Run/grid configuration and the run manifest (PRD 6.1, 6.4, 6.5b).

The manifest pins the run-level state that makes runs comparable: guiding mode,
guide-configuration identity + physical placement, mirror-lock state, site, and
the sensor-angle convention. A per-frame sidecar table supplies the node ID,
approach tag, and burst tag that NINA does not natively write to FITS headers
(PRD 6.4, 9).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional

__all__ = ["GuideConfig", "Site", "RunManifest", "GridNode", "load_manifest"]


@dataclass
class GuideConfig:
    """Guide-configuration identity and placement (PRD 6.5b)."""

    config_id: str                       # human label, e.g. "GS-60mm-toprings", "OAG"
    mode: str                            # "off" | "guidescope" | "oag"
    model_or_fl: str = ""                # guidescope model / focal length, or "OAG"
    mounting: str = ""                   # rings on OTA, side-by-side bar, saddle, ...
    mount_point: str = ""                # where on the rig it attaches

    def __post_init__(self):
        allowed = {"off", "guidescope", "oag"}
        if self.mode not in allowed:
            raise ValueError(f"guiding mode must be one of {allowed}, got {self.mode!r}")


@dataclass
class Site:
    latitude_deg: float
    longitude_deg: float                 # East-positive
    elevation_m: float = 0.0
    name: str = ""


@dataclass
class RunManifest:
    """Run-level state recorded once per survey run."""

    run_id: str
    date: str                            # ISO date of the run
    site: Site
    guide: GuideConfig
    mirror_lock_state: str = "unknown"   # "on" | "off" | "unknown"
    grid_id: str = ""
    seeing_notes: str = ""
    # Sensor-angle convention for the scalar-angle fallback path (PRD 6.4a, 9).
    sensor_angle_sign: int = 1
    sensor_angle_offset_deg: float = 0.0
    # If a WCS is present per frame it is preferred over the scalar convention.
    prefer_wcs_angle: bool = True

    def to_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)


@dataclass
class GridNode:
    node_id: str
    alt_deg: float
    az_deg: float


def load_manifest(path: str) -> RunManifest:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    site = Site(**raw.pop("site"))
    guide = GuideConfig(**raw.pop("guide"))
    return RunManifest(site=site, guide=guide, **raw)
