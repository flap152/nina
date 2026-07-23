"""Thin client for the NINA Advanced API v2 (`christian-photo/ninaAPI`).

Only the endpoints the surveyor needs are wrapped. All routes are the literal
ones observed in the ninaAPI v2 surface; the ones that still need confirmation
against a live instance are flagged in ``docs/nina-live-orchestration.md`` and in
docstrings here.

Design invariants this client makes easy to honor:

* :meth:`slew_radec` hits ``equipment/mount/slew`` — a **raw** GoTo. There is no
  method here that performs NINA's Slew-and-Center; that is deliberate (the node
  loop must never recenter — it would overwrite the controlled approach).
* :meth:`capture` requests a plate solve via the ``solve`` flag and reads the
  ``PlateSolveResult`` (``Rotation``) out of the response. A capture-solve does
  **not** move the mount; there is intentionally no sync/reslew call.

Transport is stdlib ``urllib`` (no third-party HTTP dependency). Responses follow
ninaAPI's envelope ``{"Response": ..., "Success": bool, "StatusCode": int, ...}``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

__all__ = ["NinaClient", "NinaApiError", "PlateSolveReadout", "MountInfo"]

# NOTE: the ninaAPI ``equipment/mount/slew`` route takes ``ra`` in DEGREES in the
# reference client (which multiplies input hours by 15 before sending). Confirm
# on a live instance; if the endpoint actually wants hours, set RA_IN_DEGREES
# False. A wrong choice here mis-points every slew by a factor of 15.
RA_IN_DEGREES = True


class NinaApiError(RuntimeError):
    """Raised when a NINA API call fails or returns Success=false."""


@dataclass
class PlateSolveReadout:
    """The read-only solve result extracted from a capture response."""

    rotation_deg: Optional[float]
    ra_deg: Optional[float]
    dec_deg: Optional[float]
    pixel_scale: Optional[float]
    raw: Dict[str, Any]


@dataclass
class MountInfo:
    pier_side: Optional[str]
    ra_deg: Optional[float]
    dec_deg: Optional[float]
    tracking: Optional[bool]
    raw: Dict[str, Any]


class NinaClient:
    """Minimal REST client for the endpoints the surveyor uses."""

    def __init__(self, host: str = "localhost", port: int = 1888,
                 api_key: Optional[str] = None, timeout_s: float = 180.0,
                 opener=None):
        self.base = f"http://{host}:{port}/v2/api"
        self.api_key = api_key
        self.timeout_s = timeout_s
        # ``opener`` lets tests inject a fake transport (must expose .open(url)->obj
        # with .read()). Defaults to urllib.
        self._opener = opener or urllib.request.build_opener()

    # -- transport -------------------------------------------------------- #

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base}/{endpoint}"
        if params:
            # Booleans must be lowercase strings for the API.
            norm = {k: (str(v).lower() if isinstance(v, bool) else v)
                    for k, v in params.items() if v is not None}
            url = f"{url}?{urllib.parse.urlencode(norm)}"
        req = urllib.request.Request(url)
        if self.api_key:
            req.add_header("apikey", self.api_key)
        try:
            resp = self._opener.open(req, timeout=self.timeout_s)
            body = resp.read()
        except TypeError:
            # Fake openers in tests may not accept a timeout kwarg.
            resp = self._opener.open(req)
            body = resp.read()
        except Exception as exc:  # network / HTTP errors
            raise NinaApiError(f"GET {url} failed: {exc}") from exc

        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise NinaApiError(f"GET {url}: non-JSON response: {body[:200]!r}") from exc
        if isinstance(data, dict) and data.get("Success") is False:
            raise NinaApiError(f"GET {url}: {data.get('Error') or data}")
        return data.get("Response", data) if isinstance(data, dict) else data

    # -- version / health ------------------------------------------------- #

    def version(self) -> Any:
        return self._get("version")

    # -- mount ------------------------------------------------------------ #

    def mount_info(self) -> MountInfo:
        r = self._get("equipment/mount/info") or {}
        return MountInfo(
            pier_side=_first(r, ["SideOfPier", "PierSide", "sideOfPier"]),
            ra_deg=_coord(r, ["RightAscension", "Ra", "RADeg"], to_deg_from_hours=True),
            dec_deg=_coord(r, ["Declination", "Dec", "DecDeg"]),
            tracking=_first(r, ["TrackingEnabled", "Tracking"]),
            raw=r if isinstance(r, dict) else {"raw": r},
        )

    def slew_radec(self, ra_deg: float, dec_deg: float, wait: bool = True) -> Any:
        """Issue a **raw** slew (GoTo) to RA/Dec. Not a centering slew.

        Parameters
        ----------
        ra_deg, dec_deg : float
            Target in degrees. RA is sent per :data:`RA_IN_DEGREES`.
        wait : bool
            Ask the API to block until the slew completes (``waitForResult``).
        """
        ra_param = ra_deg if RA_IN_DEGREES else ra_deg / 15.0
        return self._get("equipment/mount/slew",
                         {"ra": ra_param, "dec": dec_deg, "waitForResult": wait})

    def stop_slew(self) -> Any:
        return self._get("equipment/mount/stop-slew")

    def set_tracking(self, mode: str = "Sidereal") -> Any:
        # tracking?mode=... ; sidereal is the survey default (rate held constant).
        return self._get("equipment/mount/tracking", {"mode": mode})

    # -- camera ----------------------------------------------------------- #

    def capture(
        self,
        exposure_s: float,
        *,
        gain: Optional[int] = None,
        binning: Optional[str] = None,
        filter_name: Optional[str] = None,
        save: bool = True,
        filename: Optional[str] = None,
        solve: bool = False,
        solve_timeout_s: Optional[int] = None,
        wait: bool = True,
    ) -> Dict[str, Any]:
        """Capture one exposure, optionally plate-solving it (read-only).

        With ``solve=True`` the response carries a ``PlateSolveResult`` whose
        ``Rotation`` is the sensor angle we record. This solve does **not** sync
        or move the mount.
        """
        params = {
            "exposure": exposure_s,
            "gain": gain,
            "binning": binning,
            "filter": filter_name,
            "save": save,
            "filename": filename,
            "solve": solve,
            "solve_timeout": solve_timeout_s,
            "waitForResult": wait,
            # Keep the payload light; we read pixels from the saved FITS, not here.
            "omitImage": True,
        }
        r = self._get("equipment/camera/capture", params)
        return r if isinstance(r, dict) else {"Response": r}

    # -- guider ----------------------------------------------------------- #

    def guider_info(self) -> Dict[str, Any]:
        r = self._get("equipment/guider/info")
        return r if isinstance(r, dict) else {"Response": r}

    def start_guiding(self, calibrate: bool = False, wait: bool = True) -> Any:
        """Start/resume guiding. ``calibrate=False`` MUST be used between node
        approaches — recalibrating would reset the guide reference we are
        measuring against (PRD: guidescope stiction lives in that reference).
        """
        return self._get("equipment/guider/start",
                         {"calibrate": calibrate, "waitForResult": wait})

    def stop_guiding(self) -> Any:
        return self._get("equipment/guider/stop")

    @staticmethod
    def extract_guide_rms(info: Dict[str, Any]) -> Optional[float]:
        """Best-effort total guide RMS (pixels) from an ``equipment/guider/info``."""
        if not isinstance(info, dict):
            return None
        # Flat spellings.
        v = _first(info, ["RMSTotal", "TotalRMS", "rmsTotal"])
        if v is not None:
            return _as_float(v)
        # Nested { "RMS": { "Total": ... } } shapes.
        rms = info.get("RMS") or info.get("rms")
        if isinstance(rms, dict):
            return _as_float(_first(rms, ["Total", "total", "RA", "Dec"]))
        return _as_float(rms) if rms is not None else None

    @staticmethod
    def extract_solve(capture_response: Dict[str, Any]) -> PlateSolveReadout:
        """Pull the PlateSolveResult (Rotation, RA, Dec, PixelScale) out."""
        psr = None
        if isinstance(capture_response, dict):
            psr = capture_response.get("PlateSolveResult") or capture_response.get("plateSolveResult")
        psr = psr or {}
        return PlateSolveReadout(
            rotation_deg=_first(psr, ["Rotation", "PositionAngle", "Orientation"]),
            ra_deg=_coord(psr, ["Ra", "RA", "RADeg"], to_deg_from_hours=True),
            dec_deg=_coord(psr, ["Dec", "Declination", "DecDeg"]),
            pixel_scale=_first(psr, ["PixelScale", "Pixscale"]),
            raw=psr,
        )


def _first(d: Dict[str, Any], keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coord(d, keys, to_deg_from_hours: bool = False):
    v = _first(d, keys)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    # Heuristic: RA fields are sometimes hours (0-24) and sometimes degrees.
    if to_deg_from_hours and 0.0 <= v <= 24.0001:
        return v * 15.0
    return v
