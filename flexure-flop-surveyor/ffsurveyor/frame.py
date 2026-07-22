"""Per-frame orchestration: pixels -> gravity-frame elongation vector.

Ties together detection (:mod:`ffsurveyor.detect`), solving
(:mod:`ffsurveyor.solve`), and the angle transform (:mod:`ffsurveyor.angles`)
into a single per-frame result carrying the elongation vector in the gravity
frame (PRD 6.5 step 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .angles import (
    DoubleAngleVector,
    SensorAngleConvention,
    elongation_to_double_angle,
    parallactic_angle_deg,
    rotate_double_angle,
    sky_to_gravity_double_angle,
)
from .config import Site
from .detect import DetectionConfig, FrameElongation, measure_frame
from .solve import FrameSolution

__all__ = ["FrameResult", "local_sidereal_time_deg", "process_frame"]


@dataclass
class FrameResult:
    """Everything the aggregation stage needs from one frame."""

    ra_deg: float
    dec_deg: float
    alt_deg: Optional[float]
    az_deg: Optional[float]
    hour_angle_deg: float
    parallactic_deg: float
    sensor_pa_deg: float
    sky_pa_deg: float
    # Both frames are retained (PRD point 9): sensor-fixed errors (tilt, pinched
    # optic) are coherent in the sensor frame; gravity-driven errors are coherent
    # in the gravity frame. Aggregation runs in parallel on both.
    sensor_vector: DoubleAngleVector
    gravity_vector: DoubleAngleVector
    gravity_pa_deg: float
    magnitude: float
    n_stars: int
    solve_source: str

    # populated by the ingest layer from the sidecar:
    node_id: str = ""
    approach: str = ""
    burst: str = ""
    timestamp: str = ""


def local_sidereal_time_deg(date_obs_iso: str, longitude_deg: float) -> float:
    """Apparent local sidereal time in degrees for a UTC timestamp."""
    from astropy.time import Time
    import astropy.units as u
    from astropy.coordinates import Longitude

    t = Time(date_obs_iso, scale="utc")
    lst = t.sidereal_time("apparent", longitude=Longitude(longitude_deg * u.deg))
    return float(lst.deg)


def _altaz(ra_deg, dec_deg, date_obs_iso, site: Site):
    from astropy.coordinates import SkyCoord, EarthLocation, AltAz
    from astropy.time import Time
    import astropy.units as u

    loc = EarthLocation(
        lat=site.latitude_deg * u.deg,
        lon=site.longitude_deg * u.deg,
        height=site.elevation_m * u.m,
    )
    t = Time(date_obs_iso, scale="utc")
    c = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    altaz = c.transform_to(AltAz(obstime=t, location=loc))
    return float(altaz.alt.deg), float(altaz.az.deg)


def process_frame(
    image: np.ndarray,
    solution: FrameSolution,
    date_obs_iso: str,
    site: Site,
    detection: Optional[DetectionConfig] = None,
    sensor_convention: Optional[SensorAngleConvention] = None,
    use_wcs_angle: bool = True,
    scalar_solver_angle_deg: Optional[float] = None,
) -> FrameResult:
    """Process one frame to a gravity-frame elongation vector.

    Parameters
    ----------
    image : 2D ndarray
    solution : FrameSolution
        Plate solution for this frame (provides pointing and WCS rotation).
    date_obs_iso : str
        UTC observation timestamp (FITS ``DATE-OBS``).
    site : Site
    detection : DetectionConfig, optional
    sensor_convention : SensorAngleConvention, optional
        Used only on the scalar-angle fallback path.
    use_wcs_angle : bool
        If True (default) and the solution carries a WCS, rotate sensor->sky via
        the WCS Jacobian. Else use ``scalar_solver_angle_deg`` + convention.
    scalar_solver_angle_deg : float, optional
        Solver-reported sensor angle for the scalar fallback path.
    """
    elong: FrameElongation = measure_frame(image, detection)

    ra, dec = solution.ra_deg, solution.dec_deg
    lst = local_sidereal_time_deg(date_obs_iso, site.longitude_deg)
    hour_angle = ((lst - ra + 180.0) % 360.0) - 180.0  # degrees, positive west
    q = float(parallactic_angle_deg(hour_angle, dec, site.latitude_deg))

    if use_wcs_angle and solution._wcs is not None:
        # WCS path: map the aggregate sensor PA onto the sky, then rotate by -q.
        sky_pa = solution.sky_pa_of_sensor_pa(elong.pa_deg)
        sky_vec = elongation_to_double_angle(elong.magnitude, sky_pa)
        gravity = rotate_double_angle(sky_vec, -q)
    else:
        # Scalar fallback (M0 / no WCS): apply solver angle via the convention.
        conv = sensor_convention or SensorAngleConvention()
        angle = scalar_solver_angle_deg if scalar_solver_angle_deg is not None else 0.0
        gravity = sky_to_gravity_double_angle(elong.vector, angle, q, conv)
        from .angles import sensor_pa_to_sky_pa
        sky_pa = float(sensor_pa_to_sky_pa(elong.pa_deg + angle * 0.0, conv))  # informational

    try:
        alt, az = _altaz(ra, dec, date_obs_iso, site)
    except Exception:
        alt, az = None, None

    return FrameResult(
        ra_deg=ra,
        dec_deg=dec,
        alt_deg=alt,
        az_deg=az,
        hour_angle_deg=hour_angle,
        parallactic_deg=q,
        sensor_pa_deg=elong.pa_deg,
        sky_pa_deg=float(sky_pa),
        sensor_vector=elong.vector,
        gravity_vector=gravity,
        gravity_pa_deg=float(gravity.pa_deg),
        magnitude=float(gravity.magnitude),
        n_stars=elong.n_stars,
        solve_source=solution.source,
    )
