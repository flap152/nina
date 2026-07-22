"""FITS + sidecar ingestion and results export (PRD 6.4, 6.7).

NINA does not natively tag frames with node ID / approach / burst, so those come
from a **sidecar CSV** keyed by FITS filename (PRD 6.4, 9). Everything else the
analyzer needs — pointing and sensor rotation — it derives by plate-solving each
frame itself (PRD 6.4a).

Sidecar CSV columns (header row required):
    filename, node_id, approach, burst
Optional extra columns are ignored. ``filename`` matches the FITS basename.
"""

from __future__ import annotations

import csv
import glob
import json
import os
from dataclasses import asdict
from typing import Dict, List, Optional

import numpy as np

from .aggregate import NodeAggregate
from .config import RunManifest, Site
from .detect import DetectionConfig
from .frame import FrameResult, process_frame
from .solve import solve_frame

__all__ = ["read_sidecar", "ingest_run", "write_node_results", "IngestReport"]


def read_sidecar(path: str) -> Dict[str, dict]:
    """Read the sidecar CSV into {basename: row-dict}."""
    rows: Dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"filename", "node_id", "approach", "burst"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"sidecar missing columns: {sorted(missing)}")
        for row in reader:
            rows[os.path.basename(row["filename"].strip())] = row
    return rows


class IngestReport:
    """Accumulates per-frame outcomes for user-facing reporting."""

    def __init__(self):
        self.processed: List[FrameResult] = []
        self.skipped: List[tuple] = []   # (filename, reason)

    def summary(self) -> str:
        return (
            f"{len(self.processed)} frame(s) processed, "
            f"{len(self.skipped)} skipped."
        )


def ingest_run(
    manifest: RunManifest,
    fits_glob: str,
    sidecar_path: str,
    external_solver: Optional[str] = None,
    detection: Optional[DetectionConfig] = None,
    report: Optional[IngestReport] = None,
) -> IngestReport:
    """Ingest all frames of a run into FrameResults (PRD 5.2).

    Parameters
    ----------
    manifest : RunManifest
    fits_glob : str
        Glob for the run's FITS files (e.g. ``/data/run1/*.fits``).
    sidecar_path : str
        Path to the node/approach/burst sidecar CSV.
    external_solver : {"astap", None}
        External plate solver to use when a frame has no embedded WCS.
    detection : DetectionConfig, optional
    report : IngestReport, optional
    """
    from astropy.io import fits

    if report is None:
        report = IngestReport()
    sidecar = read_sidecar(sidecar_path)

    from .angles import SensorAngleConvention
    conv = SensorAngleConvention(
        sign=manifest.sensor_angle_sign,
        offset_deg=manifest.sensor_angle_offset_deg,
    )

    for path in sorted(glob.glob(fits_glob)):
        base = os.path.basename(path)
        meta = sidecar.get(base)
        if meta is None:
            report.skipped.append((base, "no sidecar row"))
            continue
        try:
            with fits.open(path, memmap=False) as hdul:
                hdu = _first_image_hdu(hdul)
                data = np.asarray(hdu.data, dtype=float)
                header = hdu.header
            date_obs = header.get("DATE-OBS") or header.get("DATE-AVG")
            if not date_obs:
                report.skipped.append((base, "no DATE-OBS"))
                continue

            solution = solve_frame(path, header, data.shape, external=external_solver)
            if solution is None:
                report.skipped.append((base, "plate solve failed (no WCS)"))
                continue

            result = process_frame(
                data,
                solution,
                date_obs_iso=date_obs,
                site=manifest.site,
                detection=detection,
                sensor_convention=conv,
                use_wcs_angle=manifest.prefer_wcs_angle,
            )
            result.node_id = meta["node_id"].strip()
            result.approach = meta["approach"].strip()
            result.burst = meta["burst"].strip()
            result.timestamp = date_obs
            report.processed.append(result)
        except Exception as exc:  # keep the run going; record why
            report.skipped.append((base, f"{type(exc).__name__}: {exc}"))
    return report


def _first_image_hdu(hdul):
    for hdu in hdul:
        if getattr(hdu, "data", None) is not None and np.ndim(hdu.data) == 2:
            return hdu
    return hdul[0]


def write_node_results(nodes: List[NodeAggregate], path: str) -> None:
    """Write per-node aggregates to CSV or JSON (by extension), PRD 6.7."""
    records = []
    for n in nodes:
        records.append({
            "node_id": n.node_id,
            "alt_deg": n.alt_deg,
            "az_deg": n.az_deg,
            "flexure_mag": n.flexure_mag,
            "flexure_pa_deg": n.flexure_pa_deg,
            "hysteresis_mag": n.hysteresis_mag,
            "sensor_flexure_mag": n.sensor_flexure_mag,
            "sensor_flexure_pa_deg": n.sensor_flexure_pa_deg,
            "sensor_hysteresis_mag": n.sensor_hysteresis_mag,
            "mean_repeatability": n.mean_repeatability,
            "approaches": "|".join(n.approaches),
            "n_frames": n.n_frames,
        })
    if path.lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2)
    else:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()) if records else [])
            writer.writeheader()
            writer.writerows(records)
