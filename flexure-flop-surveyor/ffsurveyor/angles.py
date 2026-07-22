"""Angle math for elongation vectors and the sensor -> sky -> gravity transform.

This module is the mathematically load-bearing core of the analyzer (PRD 6.4a,
6.5). Everything here is deliberately convention-explicit and independently
testable, because a sign error in any of these rotations silently mirrors the
whole vector field (PRD 9).

Two ideas do all the work:

1. **Double-angle representation of elongation.**
   A star's elongation has a position angle theta that is 180-deg ambiguous: an
   ellipse oriented at theta and at theta+180 is identical. Averaging or
   differencing such angles directly wraps incorrectly (a star at 179 deg and
   one at 1 deg must not average to 90 deg). We therefore carry elongation as a
   2-vector

       e = m * (cos 2*theta, sin 2*theta)

   where ``m`` is the (non-negative) elongation magnitude. In this space,
   ordinary vector mean / difference are the correct averaging and differencing
   operations, and a physical rotation of the frame by an angle ``phi`` rotates
   the double-angle vector by ``2*phi``.

2. **Parallactic angle** rotates the sky (equatorial, PA measured from celestial
   north) into the gravity frame (PA measured from the local vertical / zenith).

Angle conventions used throughout:
  * Position angle (PA) is measured in degrees, counter-clockwise, from a
    reference "up" axis toward the "left" axis, i.e. the standard astronomical
    North-through-East convention when the reference is celestial north.
  * The plate-solved sensor angle is described by :class:`SensorAngleConvention`
    so a specific plate solver's sign/offset can be pinned once and validated
    (PRD 9) rather than hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "DoubleAngleVector",
    "elongation_to_double_angle",
    "double_angle_to_pa_mag",
    "rotate_double_angle",
    "parallactic_angle_deg",
    "SensorAngleConvention",
    "sensor_pa_to_sky_pa",
    "sky_to_gravity_double_angle",
]


@dataclass(frozen=True)
class DoubleAngleVector:
    """An elongation expressed in double-angle space.

    Attributes
    ----------
    x, y : float or ndarray
        Cartesian components ``m*cos(2*theta)`` and ``m*sin(2*theta)``.
    """

    x: np.ndarray
    y: np.ndarray

    @property
    def magnitude(self) -> np.ndarray:
        return np.hypot(self.x, self.y)

    @property
    def pa_deg(self) -> np.ndarray:
        """Position angle in degrees, folded to [0, 180)."""
        return (np.degrees(0.5 * np.arctan2(self.y, self.x))) % 180.0


def elongation_to_double_angle(magnitude, pa_deg) -> DoubleAngleVector:
    """Build a double-angle vector from a magnitude and a position angle.

    Parameters
    ----------
    magnitude : array_like
        Non-negative elongation magnitude (e.g. eccentricity or (a-b)/(a+b)).
    pa_deg : array_like
        Position angle in degrees. The 180-deg ambiguity is handled by the
        double-angle mapping, so any representative of the orientation is fine.
    """
    magnitude = np.asarray(magnitude, dtype=float)
    two_theta = np.radians(2.0 * np.asarray(pa_deg, dtype=float))
    return DoubleAngleVector(magnitude * np.cos(two_theta), magnitude * np.sin(two_theta))


def double_angle_to_pa_mag(vec: DoubleAngleVector):
    """Inverse of :func:`elongation_to_double_angle`.

    Returns
    -------
    (magnitude, pa_deg) : tuple of ndarray
        ``pa_deg`` folded to [0, 180).
    """
    return vec.magnitude, vec.pa_deg


def rotate_double_angle(vec: DoubleAngleVector, phi_deg) -> DoubleAngleVector:
    """Rotate the underlying orientation frame by ``phi_deg``.

    A physical rotation of the reference frame by ``phi`` degrees maps a
    position angle ``theta -> theta + phi``, which rotates the double-angle
    vector by ``2*phi``.
    """
    two_phi = np.radians(2.0 * np.asarray(phi_deg, dtype=float))
    c, s = np.cos(two_phi), np.sin(two_phi)
    return DoubleAngleVector(c * vec.x - s * vec.y, s * vec.x + c * vec.y)


def parallactic_angle_deg(hour_angle_deg, dec_deg, latitude_deg):
    """Parallactic angle q, in degrees, from hour angle / declination / latitude.

    The parallactic angle is the position angle of the local vertical (the
    direction toward the zenith) measured at the star from celestial north,
    counter-clockwise toward east. Rotating a sky-frame PA by ``-q`` therefore
    expresses it in the gravity frame (PA measured from the vertical).

    Formula (Meeus, *Astronomical Algorithms*, 2nd ed., Ch. 14)::

        q = atan2( sin H,  tan(phi) cos(dec) - sin(dec) cos H )

    with ``H`` the local hour angle (positive toward the west), ``dec`` the
    declination, and ``phi`` the observer's geographic latitude.

    Parameters
    ----------
    hour_angle_deg : array_like
        Local hour angle in degrees (positive west of the meridian).
    dec_deg, latitude_deg : array_like
        Declination and observer latitude in degrees.

    Returns
    -------
    ndarray
        Parallactic angle in degrees in (-180, 180].
    """
    H = np.radians(np.asarray(hour_angle_deg, dtype=float))
    dec = np.radians(np.asarray(dec_deg, dtype=float))
    lat = np.radians(np.asarray(latitude_deg, dtype=float))
    y = np.sin(H)
    x = np.tan(lat) * np.cos(dec) - np.sin(dec) * np.cos(H)
    return np.degrees(np.arctan2(y, x))


@dataclass(frozen=True)
class SensorAngleConvention:
    """Maps a plate solver's reported angle to a celestial-north sky PA.

    Different solvers report the sensor rotation with different signs and zero
    points. Pin this once for a given capture setup and validate it (PRD 9)
    instead of trusting a guessed convention.

    The mapping applied is::

        sky_pa = sign * solver_angle + offset_deg

    Parameters
    ----------
    sign : {+1, -1}
        +1 if the solver measures PA in the same (North-through-East, CCW)
        sense used here; -1 if it is mirrored (e.g. a flipped/"Orientation"
        value such as NINA's obsolete ``360 - PositionAngle``).
    offset_deg : float
        Constant zero-point offset added after applying ``sign``.
    """

    sign: int = 1
    offset_deg: float = 0.0

    def __post_init__(self):
        if self.sign not in (1, -1):
            raise ValueError("sign must be +1 or -1")


def sensor_pa_to_sky_pa(solver_angle_deg, convention: SensorAngleConvention):
    """Convert a plate-solved sensor angle to a celestial-north sky PA (deg)."""
    solver_angle_deg = np.asarray(solver_angle_deg, dtype=float)
    return (convention.sign * solver_angle_deg + convention.offset_deg) % 360.0


def sky_to_gravity_double_angle(
    vec_sensor: DoubleAngleVector,
    solver_angle_deg,
    parallactic_angle_deg_value,
    convention: SensorAngleConvention,
) -> DoubleAngleVector:
    """Rotate a sensor-frame elongation vector into the gravity (alt/az) frame.

    This is the full PRD 6.4a / 6.5-step-4 transform, applied in two rotations:

        sensor frame  --(+sky_pa_offset)-->  sky frame  --(-q)-->  gravity frame

    where ``sky_pa_offset`` is the sensor-to-sky rotation implied by the plate
    solve and ``q`` is the parallactic angle. Because we operate in double-angle
    space, each physical rotation ``phi`` is applied as a ``2*phi`` rotation by
    :func:`rotate_double_angle`.

    Parameters
    ----------
    vec_sensor : DoubleAngleVector
        Elongation vector measured in sensor/pixel coordinates.
    solver_angle_deg : float
        The plate solver's reported sensor rotation angle for this frame.
    parallactic_angle_deg_value : float
        Parallactic angle q for the frame's pointing (see
        :func:`parallactic_angle_deg`).
    convention : SensorAngleConvention
        How to interpret ``solver_angle_deg``.

    Returns
    -------
    DoubleAngleVector
        Elongation vector in the gravity frame, PA measured from the local
        vertical, CCW toward the horizon-left direction.
    """
    sky_pa_offset = sensor_pa_to_sky_pa(solver_angle_deg, convention)
    vec_sky = rotate_double_angle(vec_sensor, sky_pa_offset)
    vec_grav = rotate_double_angle(vec_sky, -np.asarray(parallactic_angle_deg_value, dtype=float))
    return vec_grav
