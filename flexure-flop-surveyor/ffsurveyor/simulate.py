"""Synthetic ground-truth run generator (closed-loop self-test).

No NINA simulator source is pointing-aware *and* elongation-controllable (the
Camera Simulator's Directory mode just replays files; SkySurvey pulls real DSS
imagery). So to validate the *measurement* we generate our own run: FITS frames
whose pixels encode a **known** gravity-frame flexure + flop field, with a
correct WCS, plus the matching sidecar + manifest. Running ``ffsurvey analyze``
on the output must recover the injected field — a check with a known answer.

The injection is done through the analyzer's *own* transform so the simulator and
analyzer cannot disagree by construction: for each frame we build a rotation WCS,
ask the analyzer what sky PA a given sensor orientation maps to, and invert that
(plus the parallactic angle) to find the sensor orientation to draw for a desired
gravity-frame elongation.

The same FITS folder can be pointed at NINA's Camera Simulator "Directory" source
to exercise the live REST capture path (that path is validated separately by
``ffsurvey doctor`` / the integration test).
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .angles import DoubleAngleVector, elongation_to_double_angle, parallactic_angle_deg
from .capture.planner import SurveyPlan, build_survey_plan
from .config import RunManifest, Site
from .frame import local_sidereal_time_deg
from .solve import solve_from_header

__all__ = ["InjectionModel", "simulate_run"]


@dataclass
class InjectionModel:
    """The ground-truth error field to inject (gravity frame)."""

    flexure_amp: float = 0.6        # peak flexure eccentricity (near horizon)
    flop_amp: float = 0.15          # per-approach flop vector magnitude
    flop_pa_grav_deg: float = 30.0  # gravity-frame PA of the flop component
    seeing_sigma: float = 0.03      # per-frame random scatter (eccentricity units)
    sensor_rotation_deg: float = 33.0   # constant camera angle on the sky
    rotation_jitter_deg: float = 0.0    # per-frame angle wobble (e.g. SCT refocus)
    pixscale_arcsec: float = 1.0
    frame_shape: Tuple[int, int] = (256, 256)
    n_stars: int = 25
    base_sigma_px: float = 2.2      # minor-axis Gaussian sigma

    def flexure_ecc(self, alt_deg: float) -> float:
        """Flexure eccentricity vs altitude: worst near the horizon (PRD 2)."""
        return float(np.clip(self.flexure_amp * np.cos(np.radians(alt_deg)), 0.0, 0.93))


def _rotation_wcs_header(ra_deg, dec_deg, rot_deg, scale_arcsec, shape):
    """A pure-rotation (parity-preserving) TAN WCS, so sky rotation is conformal."""
    s = scale_arcsec / 3600.0
    r = np.radians(rot_deg)
    ny, nx = shape
    return {
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
        "CUNIT1": "deg", "CUNIT2": "deg",
        "CRPIX1": nx / 2.0, "CRPIX2": ny / 2.0,
        "CRVAL1": ra_deg, "CRVAL2": dec_deg,
        # det > 0 (no flip) => conformal rotation; angles preserved.
        "CD1_1": s * np.cos(r), "CD1_2": -s * np.sin(r),
        "CD2_1": s * np.sin(r), "CD2_2": s * np.cos(r),
    }


def _elliptical_gaussian(shape, x0, y0, sig_a, sig_b, pa_deg, amp):
    ny, nx = shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    t = np.radians(pa_deg)
    dx, dy = xx - x0, yy - y0
    xr = dx * np.cos(t) + dy * np.sin(t)
    yr = -dx * np.sin(t) + dy * np.cos(t)
    return amp * np.exp(-0.5 * ((xr / sig_a) ** 2 + (yr / sig_b) ** 2))


def _altaz_to_radec(alt_deg, az_deg, iso, site: Site):
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    import astropy.units as u

    loc = EarthLocation(lat=site.latitude_deg * u.deg, lon=site.longitude_deg * u.deg,
                        height=site.elevation_m * u.m)
    t = Time(iso, scale="utc")
    altaz = AltAz(az=az_deg * u.deg, alt=alt_deg * u.deg, obstime=t, location=loc)
    icrs = SkyCoord(altaz).icrs
    return float(icrs.ra.deg), float(icrs.dec.deg)


def simulate_run(
    out_dir: str,
    manifest: RunManifest,
    model: Optional[InjectionModel] = None,
    plan: Optional[SurveyPlan] = None,
    date_iso: str = "2026-01-15T09:00:00",
    burst_count: int = 3,
    bursts: Tuple[str, ...] = ("post_settle",),
    seed: int = 0,
) -> dict:
    """Generate a full synthetic run (FITS + sidecar + manifest) with known truth.

    Returns paths to the sidecar and manifest, and the ground-truth per node.
    """
    from astropy.io import fits

    model = model or InjectionModel()
    if plan is None:
        plan = build_survey_plan(alt_bands_deg=(30.0, 60.0), azimuths_deg=(90.0, 180.0))
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(seed)

    flop_vec = elongation_to_double_angle(model.flop_amp, model.flop_pa_grav_deg)
    rows: List[dict] = []
    truth: List[dict] = []
    fidx = 0

    for node in plan.nodes:
        ra, dec = _altaz_to_radec(node.alt_deg, node.az_deg, date_iso, site=manifest.site)
        lst = local_sidereal_time_deg(date_iso, manifest.site.longitude_deg)
        ha = ((lst - ra + 180.0) % 360.0) - 180.0
        q = float(parallactic_angle_deg(ha, dec, manifest.site.latitude_deg))
        flex_ecc = model.flexure_ecc(node.alt_deg)
        # Flexure points along the local vertical => gravity-frame PA = 0.
        flex_vec = elongation_to_double_angle(flex_ecc, 0.0)
        truth.append({"node_id": node.node_id, "alt_deg": node.alt_deg,
                      "flexure_ecc": flex_ecc, "flexure_pa_grav": 0.0,
                      "hysteresis_expected": 2.0 * model.flop_amp})

        for approach in node.approaches:
            sign = +1 if approach.offset_sign > 0 else -1
            for burst in bursts:
                for _ in range(burst_count):
                    # Desired gravity-frame elongation for this frame.
                    jx = rng.normal(0.0, model.seeing_sigma)
                    jy = rng.normal(0.0, model.seeing_sigma)
                    gvec = DoubleAngleVector(
                        flex_vec.x + sign * flop_vec.x + jx,
                        flex_vec.y + sign * flop_vec.y + jy,
                    )
                    mag = float(np.clip(gvec.magnitude, 0.0, 0.93))
                    gravity_pa = float(gvec.pa_deg)

                    rot = model.sensor_rotation_deg + rng.normal(0.0, model.rotation_jitter_deg)
                    header = _rotation_wcs_header(ra, dec, rot, model.pixscale_arcsec,
                                                  model.frame_shape)
                    # Invert through the analyzer's own transform. For a conformal
                    # rotation WCS the analyzer maps sky_pa = rot_measured - theta
                    # (the atan2(cos,sin) flips theta's sign), and
                    # gravity_pa = sky_pa - q. Solve for the sensor PA to draw:
                    #   theta = rot_measured - q - gravity_pa.
                    sol = solve_from_header(_as_header(header), data_shape=model.frame_shape)
                    rot_measured = sol.sky_pa_of_sensor_pa(0.0)  # sky PA at sensor theta=0
                    sensor_pa = (rot_measured - q - gravity_pa) % 180.0

                    img = _draw_field(model, sensor_pa, mag, rng)
                    fname = f"{manifest.run_id}_{fidx:04d}.fits"
                    hdu = fits.PrimaryHDU(img.astype(np.float32))
                    for k, v in header.items():
                        hdu.header[k] = v
                    hdu.header["DATE-OBS"] = date_iso
                    hdu.writeto(os.path.join(out_dir, fname), overwrite=True)

                    rows.append({"filename": fname, "node_id": node.node_id,
                                 "approach": approach.tag, "burst": burst})
                    fidx += 1

    sidecar_path = os.path.join(out_dir, "sidecar.csv")
    with open(sidecar_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "node_id", "approach", "burst"])
        w.writeheader()
        w.writerows(rows)
    manifest_path = os.path.join(out_dir, "manifest.json")
    manifest.to_json(manifest_path)

    return {"sidecar": sidecar_path, "manifest": manifest_path,
            "n_frames": len(rows), "truth": truth, "out_dir": out_dir}


def _draw_field(model: InjectionModel, sensor_pa, mag, rng):
    shape = model.frame_shape
    img = np.full(shape, 200.0)
    sig_b = model.base_sigma_px
    sig_a = sig_b / max(1e-3, np.sqrt(1.0 - min(mag, 0.93) ** 2))  # eccentricity -> axes
    margin = 24
    for _ in range(model.n_stars):
        x0 = rng.uniform(margin, shape[1] - margin)
        y0 = rng.uniform(margin, shape[0] - margin)
        amp = rng.uniform(4000, 11000)
        img += _elliptical_gaussian(shape, x0, y0, sig_a, sig_b, sensor_pa, amp)
    img += rng.normal(0.0, 10.0, size=shape)
    return img


def _as_header(d):
    from astropy.io import fits
    h = fits.Header()
    for k, v in d.items():
        h[k] = v
    return h
