"""Per-frame plate solving and the sensor->sky rotation (PRD 6.4a).

Per the chosen approach, the analyzer resolves the sensor rotation angle for
**every** frame rather than trusting a once-entered value (PRD 6.4a: SCT focus
moves the primary, so the angle is not stable across a night).

Two solve sources, in priority order:

1. **WCS already in the FITS header.** If a frame carries a valid world
   coordinate system, the rotation is read straight from it.
2. **External solver** (ASTAP or astrometry.net ``solve-field``), invoked as a
   subprocess, which writes a WCS we then read. Requires the binary to be
   installed; this is the only step that is not pure-Python/portable.

The sensor->sky rotation is derived **numerically from the WCS Jacobian** rather
than from a hand-parsed CROTA/CD sign. Mapping two pixel basis directions onto
the sky captures rotation, flip/parity, and anisotropic scale at once, which
avoids the sign-convention trap called out in PRD 9.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

import numpy as np

__all__ = ["FrameSolution", "solve_from_header", "solve_with_astap", "solve_frame"]


@dataclass
class FrameSolution:
    """Result of solving one frame."""

    ra_deg: float                  # image-center RA (ICRS), degrees
    dec_deg: float                 # image-center Dec (ICRS), degrees
    pixscale_arcsec: float
    source: str                    # "header-wcs" | "astap" | "astrometry"
    _wcs: object = None            # astropy.wcs.WCS, kept for direction mapping

    def sky_pa_of_sensor_pa(self, sensor_pa_deg: float) -> float:
        """Map a sensor-frame position angle to a sky PA (North through East).

        Uses the local WCS Jacobian at the image center. Handles rotation and
        parity automatically. Returns degrees folded to [0, 180) since an
        elongation orientation is 180-deg ambiguous.
        """
        from astropy.wcs.utils import local_partial_pixel_derivatives

        w = self._wcs
        nx, ny = _wcs_shape(w)
        x0, y0 = (nx - 1) / 2.0, (ny - 1) / 2.0

        # d(world)/d(pixel): rows = [RA, Dec] world axes, cols = [x, y] pixel axes.
        jac = local_partial_pixel_derivatives(w, x0, y0)
        dec = np.radians(self.dec_deg)
        cosd = np.cos(dec)

        theta = np.radians(sensor_pa_deg)
        # Major-axis pixel direction.
        dxdy = np.array([np.cos(theta), np.sin(theta)])
        dra = jac[0, 0] * dxdy[0] + jac[0, 1] * dxdy[1]     # deg RA per unit pixel
        ddec = jac[1, 0] * dxdy[0] + jac[1, 1] * dxdy[1]    # deg Dec
        east = dra * cosd
        north = ddec
        sky_pa = np.degrees(np.arctan2(east, north))        # N through E
        return float(sky_pa % 180.0)


def _wcs_shape(w) -> tuple:
    """Best-effort (nx, ny) for a WCS."""
    if getattr(w, "pixel_shape", None):
        return int(w.pixel_shape[0]), int(w.pixel_shape[1])
    naxis1 = w.to_header().get("NAXIS1")
    naxis2 = w.to_header().get("NAXIS2")
    if naxis1 and naxis2:
        return int(naxis1), int(naxis2)
    return 1024, 1024


def _pixscale_from_wcs(w) -> float:
    from astropy.wcs.utils import proj_plane_pixel_scales

    scales = proj_plane_pixel_scales(w)  # deg/pixel per axis
    return float(np.mean(scales) * 3600.0)


def solve_from_header(header, data_shape=None) -> Optional[FrameSolution]:
    """Build a FrameSolution from an existing WCS in a FITS header, or None."""
    from astropy.wcs import WCS

    with np.errstate(all="ignore"):
        w = WCS(header)
    if not w.has_celestial:
        return None
    w = w.celestial
    if data_shape is not None and getattr(w, "pixel_shape", None) is None:
        w.pixel_shape = (data_shape[1], data_shape[0])
    nx, ny = _wcs_shape(w)
    center = w.pixel_to_world((nx - 1) / 2.0, (ny - 1) / 2.0)
    center_icrs = center.icrs
    return FrameSolution(
        ra_deg=float(center_icrs.ra.deg),
        dec_deg=float(center_icrs.dec.deg),
        pixscale_arcsec=_pixscale_from_wcs(w),
        source="header-wcs",
        _wcs=w,
    )


def solve_with_astap(
    fits_path: str,
    astap_bin: str = "astap",
    ra_hint_deg: Optional[float] = None,
    dec_hint_deg: Optional[float] = None,
    fov_hint_deg: Optional[float] = None,
    timeout_s: int = 120,
) -> Optional[FrameSolution]:
    """Solve a FITS file with ASTAP and return a FrameSolution, or None on failure.

    ASTAP writes a ``.wcs`` file next to the input on success; we read the WCS
    from it. Requires ``astap`` (and a star database) to be installed.
    """
    from astropy.io import fits

    exe = shutil.which(astap_bin)
    if exe is None:
        return None

    args = [exe, "-f", fits_path, "-wcs"]
    if fov_hint_deg:
        args += ["-fov", f"{fov_hint_deg:.4f}"]
    if ra_hint_deg is not None and dec_hint_deg is not None:
        args += ["-ra", f"{ra_hint_deg / 15.0:.6f}", "-spd", f"{dec_hint_deg + 90.0:.6f}"]

    try:
        subprocess.run(args, timeout=timeout_s, capture_output=True, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None

    wcs_path = os.path.splitext(fits_path)[0] + ".wcs"
    if not os.path.exists(wcs_path):
        return None
    with open(wcs_path, "r", encoding="latin-1") as fh:
        hdr = fits.Header.fromstring(fh.read(), sep="\n")
    sol = solve_from_header(hdr)
    if sol is not None:
        sol.source = "astap"
    return sol


def solve_frame(
    fits_path: str,
    header,
    data_shape,
    external: Optional[str] = None,
    **solver_kwargs,
) -> Optional[FrameSolution]:
    """Solve a frame: header WCS first, then an optional external solver.

    Parameters
    ----------
    fits_path : str
        Path to the FITS file (needed if an external solver is used).
    header : astropy.io.fits.Header
        The primary header.
    data_shape : tuple
        (ny, nx) of the image.
    external : {"astap", None}
        External solver to fall back to when no header WCS is present.
    """
    sol = solve_from_header(header, data_shape=data_shape)
    if sol is not None:
        return sol
    if external == "astap":
        return solve_with_astap(fits_path, **solver_kwargs)
    return None
