"""Survey execution over the live NINA API (PRD 5.1), enforcing the invariants.

Per node, per approach leg, the runner:

1. resolves the node's RA/Dec **now** (nodes are fixed in alt/az) and the offset
   point for the leg;
2. issues an **offset-then-slew-in** using raw slews only (never Slew-and-Center)
   — the final slew-in direction is the controlled experimental variable;
3. reads the pier side and enforces **same pier side** across the two approaches;
4. captures an **immediate** burst, settles, then a **post-settle** burst;
5. requests a read-only plate solve to record the sensor rotation angle — the
   solve never syncs or reslews the mount;
6. writes the sidecar CSV (one row per frame) and the run manifest.

The mount's own anti-backlash "always approach from one side" convention must be
disabled for the survey (see docs); the offset-then-slew-in only produces
opposite final loads if the driver is not silently reversing the final leg.
"""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..config import RunManifest
from .ninaapi import NinaClient
from .planner import ApproachLeg, NodePlan, SurveyPlan

__all__ = ["RunnerConfig", "SurveyRunner", "VisitRecord"]


@dataclass
class RunnerConfig:
    exposure_s: float = 5.0
    burst_count: int = 5
    gain: Optional[int] = None
    binning: Optional[str] = None
    filter_name: Optional[str] = None
    settle_s: float = 5.0
    solve_first_of_burst_only: bool = True   # one read-only solve per burst suffices for angle
    solve_timeout_s: int = 60
    strict_pier_side: bool = False           # True => abort a node on a pier-side mismatch
    out_dir: str = "survey_run"
    dry_run: bool = False                    # plan/record without calling the mount/camera
    # Guiding (PRD 5.1). When on, guiding is resumed+settled after every slew, but
    # NEVER recalibrated mid-survey -- recalibration would reset the guide reference
    # whose stiction we are trying to measure.
    guiding: bool = False
    calibrate_at_start: bool = False         # allow ONE calibration at run start only
    guide_settle_s: float = 5.0              # dwell after resuming guiding, before capture


@dataclass
class VisitRecord:
    node_id: str
    approach: str
    pier_side: Optional[str]
    node_ra_deg: float
    node_dec_deg: float
    guide_rms: Optional[float] = None
    frames: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class SurveyRunner:
    """Drives a :class:`SurveyPlan` against a :class:`NinaClient`."""

    def __init__(
        self,
        client: NinaClient,
        manifest: RunManifest,
        plan: SurveyPlan,
        config: Optional[RunnerConfig] = None,
        clock: Optional[Callable[[], str]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        self.client = client
        self.manifest = manifest
        self.plan = plan
        self.config = config or RunnerConfig()
        self._clock = clock or _utc_now_iso
        self._sleep = sleep or _real_sleep
        self.visits: List[VisitRecord] = []
        self.sidecar_rows: List[dict] = []

    # -- coordinate resolution ------------------------------------------- #

    def _altaz_to_radec(self, alt_deg: float, az_deg: float, iso: str) -> Tuple[float, float]:
        from astropy.coordinates import AltAz, EarthLocation, SkyCoord
        from astropy.time import Time
        import astropy.units as u

        site = self.manifest.site
        loc = EarthLocation(lat=site.latitude_deg * u.deg,
                            lon=site.longitude_deg * u.deg,
                            height=site.elevation_m * u.m)
        t = Time(iso, scale="utc")
        altaz = AltAz(az=az_deg * u.deg, alt=alt_deg * u.deg, obstime=t, location=loc)
        icrs = SkyCoord(altaz).icrs
        return float(icrs.ra.deg), float(icrs.dec.deg)

    def _resolve_targets(self, node: NodePlan, leg: ApproachLeg, iso: str):
        """Return (offset_ra, offset_dec, node_ra, node_dec) for this leg."""
        node_ra, node_dec = self._altaz_to_radec(node.alt_deg, node.az_deg, iso)
        if leg.load_axis == "altitude":
            off_alt = node.alt_deg + leg.offset_sign * leg.offset_deg
            off_ra, off_dec = self._altaz_to_radec(off_alt, node.az_deg, iso)
        else:  # dec axis: offset in declination from the node's current RA/Dec
            off_ra = node_ra
            off_dec = max(-89.0, min(89.0, node_dec + leg.offset_sign * leg.offset_deg))
        return off_ra, off_dec, node_ra, node_dec

    # -- filenames -------------------------------------------------------- #

    def _filename(self, node_id: str, approach: str, burst: str, idx: int) -> str:
        return f"{self.manifest.run_id}_{node_id}_{approach}_{burst}_{idx:02d}.fits"

    # -- one burst -------------------------------------------------------- #

    def _capture_burst(self, node: NodePlan, leg: ApproachLeg, burst: str,
                       pier_side: Optional[str], node_ra: float, node_dec: float,
                       iso: str, visit: VisitRecord, guide_rms: Optional[float] = None) -> None:
        cfg = self.config
        for i in range(cfg.burst_count):
            fname = self._filename(node.node_id, leg.tag, burst, i)
            do_solve = (i == 0) if cfg.solve_first_of_burst_only else True
            rotation = None
            if not cfg.dry_run:
                resp = self.client.capture(
                    cfg.exposure_s, gain=cfg.gain, binning=cfg.binning,
                    filter_name=cfg.filter_name, save=True, filename=fname,
                    solve=do_solve, solve_timeout_s=cfg.solve_timeout_s, wait=True,
                )
                if do_solve:
                    rotation = self.client.extract_solve(resp).rotation_deg
            row = {
                "filename": fname,
                "node_id": node.node_id,
                "approach": leg.tag,
                "burst": burst,
                "timestamp": iso,
                "commanded_ra_deg": round(node_ra, 6),
                "commanded_dec_deg": round(node_dec, 6),
                "commanded_alt_deg": node.alt_deg,
                "commanded_az_deg": node.az_deg,
                "pier_side": pier_side,
                # NINA's own solve angle, recorded as a cross-check; the analyzer
                # re-solves from pixels for the authoritative value (PRD 6.4a, 9).
                "nina_rotation_deg": rotation,
                # Guide RMS at this node-visit: lets the analyst confirm guiding
                # was comparable across the runs being differenced (PRD point on
                # run consistency), and drop nodes where it wasn't.
                "guide_rms": guide_rms,
            }
            self.sidecar_rows.append(row)
            visit.frames.append(row)

    # -- one approach ----------------------------------------------------- #

    def _run_leg(self, node: NodePlan, leg: ApproachLeg,
                 reference_pier: Optional[str]) -> VisitRecord:
        iso = self._clock()
        off_ra, off_dec, node_ra, node_dec = self._resolve_targets(node, leg, iso)

        if not self.config.dry_run:
            # Offset-then-slew-in with RAW slews only (never center).
            self.client.slew_radec(off_ra, off_dec, wait=True)
            self.client.slew_radec(node_ra, node_dec, wait=True)
            pier = self.client.mount_info().pier_side
        else:
            pier = reference_pier

        visit = VisitRecord(node_id=node.node_id, approach=leg.tag, pier_side=pier,
                            node_ra_deg=node_ra, node_dec_deg=node_dec)

        if reference_pier is not None and pier is not None and pier != reference_pier:
            msg = (f"pier-side mismatch at {node.node_id}: {leg.tag} on {pier}, "
                   f"reference {reference_pier}")
            visit.warnings.append(msg)
            if self.config.strict_pier_side:
                return visit  # skip capture; caller records the aborted visit

        # For a guided run, resume+settle guiding on the new field (no recalibrate)
        # before any capture -- a guided sub is meaningless until PHD2 is settled.
        # This settle is the guide loop's; the immediate/post-settle split below is
        # the MOUNT/tube settle (backlash take-up, ring-down; PRD 6.3).
        rms = self._settle_guiding()
        visit.guide_rms = rms

        # Immediate burst (catches a flop releasing right after slew), then settle,
        # then post-settle burst (PRD 6.3). Immediate burst comes BEFORE the wait.
        self._capture_burst(node, leg, "immediate", pier, node_ra, node_dec, iso, visit, rms)
        self._sleep(self.config.settle_s)
        iso2 = self._clock()
        self._capture_burst(node, leg, "post_settle", pier, node_ra, node_dec, iso2, visit, rms)
        return visit

    def _settle_guiding(self) -> Optional[float]:
        """Resume guiding after a slew (no recalibration) and read the guide RMS."""
        if not self.config.guiding or self.config.dry_run:
            return None
        try:
            # calibrate=False is the invariant: never recalibrate mid-survey.
            self.client.start_guiding(calibrate=False, wait=True)
        except Exception:
            return None
        self._sleep(self.config.guide_settle_s)
        try:
            return self.client.extract_guide_rms(self.client.guider_info())
        except Exception:
            return None

    # -- whole survey ----------------------------------------------------- #

    def run(self) -> List[VisitRecord]:
        if not self.config.dry_run:
            # Hold tracking rate constant for the survey (rate is not a variable).
            try:
                self.client.set_tracking("Sidereal")
            except Exception:
                pass
            # Optionally calibrate the guider ONCE, at the very start. This is the
            # only place calibration is allowed; every mid-survey resume uses
            # calibrate=False so the guide reference is never reset.
            if self.config.guiding:
                try:
                    self.client.start_guiding(calibrate=self.config.calibrate_at_start, wait=True)
                except Exception:
                    pass

        for node in self.plan.nodes:
            reference_pier = None
            for leg in node.approaches:
                visit = self._run_leg(node, leg, reference_pier)
                if reference_pier is None:
                    reference_pier = visit.pier_side
                self.visits.append(visit)
        return self.visits

    # -- outputs ---------------------------------------------------------- #

    def write_outputs(self) -> Dict[str, str]:
        os.makedirs(self.config.out_dir, exist_ok=True)
        manifest_path = os.path.join(self.config.out_dir, "manifest.json")
        self.manifest.to_json(manifest_path)

        sidecar_path = os.path.join(self.config.out_dir, "sidecar.csv")
        fields = ["filename", "node_id", "approach", "burst", "timestamp",
                  "commanded_ra_deg", "commanded_dec_deg", "commanded_alt_deg",
                  "commanded_az_deg", "pier_side", "nina_rotation_deg", "guide_rms"]
        with open(sidecar_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.sidecar_rows)
        return {"manifest": manifest_path, "sidecar": sidecar_path}


def _utc_now_iso() -> str:
    from astropy.time import Time
    return Time.now().utc.isot


def _real_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)
