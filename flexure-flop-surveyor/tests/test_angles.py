"""Tests for the angle-math core.

The parallactic-angle closed form is validated against a fully independent 3D
vector-geometry computation, so the two derivations have no shared algebra to
share a bug (PRD 9: a sign error here mirrors the whole field).
"""

import numpy as np
import pytest

from ffsurveyor.angles import (
    DoubleAngleVector,
    SensorAngleConvention,
    double_angle_to_pa_mag,
    elongation_to_double_angle,
    parallactic_angle_deg,
    rotate_double_angle,
    sensor_pa_to_sky_pa,
    sky_to_gravity_double_angle,
)


# --------------------------------------------------------------------------- #
# Double-angle representation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("pa", [0.0, 30.0, 45.0, 90.0, 135.0, 179.0])
def test_double_angle_roundtrip(pa):
    vec = elongation_to_double_angle(0.4, pa)
    mag, pa_out = double_angle_to_pa_mag(vec)
    assert mag == pytest.approx(0.4)
    assert pa_out == pytest.approx(pa % 180.0)


def test_180_ambiguity_folds():
    # theta and theta+180 are the same orientation.
    a = elongation_to_double_angle(0.3, 20.0)
    b = elongation_to_double_angle(0.3, 200.0)
    assert a.x == pytest.approx(b.x)
    assert a.y == pytest.approx(b.y)


def test_wrap_safe_averaging():
    # A star at 179 deg and one at 1 deg average to ~0 deg, never 90 deg.
    a = elongation_to_double_angle(1.0, 179.0)
    b = elongation_to_double_angle(1.0, 1.0)
    mean = DoubleAngleVector(np.mean([a.x, b.x]), np.mean([a.y, b.y]))
    _, pa = double_angle_to_pa_mag(mean)
    assert min(pa, 180 - pa) < 1.0  # within a degree of 0/180, not near 90


def test_naive_averaging_would_be_wrong():
    # Guard: demonstrates why the double-angle space is necessary.
    assert np.mean([179.0, 1.0]) == pytest.approx(90.0)


def test_rotate_double_angle_matches_pa_shift():
    vec = elongation_to_double_angle(0.5, 10.0)
    rotated = rotate_double_angle(vec, 25.0)
    _, pa = double_angle_to_pa_mag(rotated)
    assert pa == pytest.approx(35.0)
    assert rotated.magnitude == pytest.approx(0.5)  # rotation preserves magnitude


# --------------------------------------------------------------------------- #
# Sensor-angle convention
# --------------------------------------------------------------------------- #

def test_sensor_convention_sign_and_offset():
    conv = SensorAngleConvention(sign=-1, offset_deg=360.0)
    # Mirrored "Orientation"-style value: 360 - PA.
    assert sensor_pa_to_sky_pa(30.0, conv) == pytest.approx(330.0)


def test_sensor_convention_rejects_bad_sign():
    with pytest.raises(ValueError):
        SensorAngleConvention(sign=2)


# --------------------------------------------------------------------------- #
# Parallactic angle vs independent 3D reference
# --------------------------------------------------------------------------- #

def _parallactic_reference(hour_angle_deg, dec_deg, latitude_deg):
    """Independent parallactic angle via 3D tangent-plane geometry.

    Works in an equatorial Cartesian frame with basis
    (meridian-equator point, +hour-angle direction, NCP). The parallactic
    angle is the signed angle at the star from the NCP tangent direction to
    the zenith tangent direction, taken about the outward star vector.
    """
    H = np.radians(hour_angle_deg)
    dec = np.radians(dec_deg)
    lat = np.radians(latitude_deg)

    s = np.array([np.cos(dec) * np.cos(H), np.cos(dec) * np.sin(H), np.sin(dec)])
    ncp = np.array([0.0, 0.0, 1.0])
    zenith = np.array([np.cos(lat), 0.0, np.sin(lat)])  # H=0, dec=lat

    def tangent(v):
        t = v - np.dot(v, s) * s
        return t / np.linalg.norm(t)

    p_t, z_t = tangent(ncp), tangent(zenith)
    cos_q = np.dot(p_t, z_t)
    sin_q = np.dot(np.cross(p_t, z_t), s)
    return np.degrees(np.arctan2(sin_q, cos_q))


@pytest.mark.parametrize(
    "H,dec,lat",
    [
        (0.0, 10.0, 45.0),    # meridian, south of zenith -> q = 0
        (0.0, 70.0, 45.0),    # meridian, north of zenith -> q = 180
        (-40.0, 20.0, 45.0),  # east of meridian -> q < 0
        (40.0, 20.0, 45.0),   # west of meridian -> q > 0
        (30.0, -15.0, 34.0),
        (-75.0, 55.0, 52.0),
        (120.0, 5.0, 40.0),
    ],
)
def test_parallactic_matches_3d_reference(H, dec, lat):
    q_closed = float(parallactic_angle_deg(H, dec, lat))
    q_ref = _parallactic_reference(H, dec, lat)
    # Compare on the circle to be robust at the +/-180 seam.
    diff = (q_closed - q_ref + 180.0) % 360.0 - 180.0
    assert diff == pytest.approx(0.0, abs=1e-6)


def test_parallactic_meridian_anchors():
    assert float(parallactic_angle_deg(0.0, 10.0, 45.0)) == pytest.approx(0.0, abs=1e-9)
    assert abs(float(parallactic_angle_deg(0.0, 70.0, 45.0))) == pytest.approx(180.0, abs=1e-9)


def test_parallactic_sign_tracks_hour_angle():
    assert float(parallactic_angle_deg(-30.0, 20.0, 45.0)) < 0  # east
    assert float(parallactic_angle_deg(30.0, 20.0, 45.0)) > 0   # west


# --------------------------------------------------------------------------- #
# Full sensor -> gravity transform
# --------------------------------------------------------------------------- #

def test_gravity_transform_recovers_known_orientation():
    # A physical elongation that is purely "vertical" (along the local
    # vertical, PA=0 in the gravity frame) must come back out at PA=0
    # regardless of sensor angle and parallactic angle, once transformed.
    conv = SensorAngleConvention(sign=1, offset_deg=0.0)
    sensor_angle = 37.0
    q = 22.0
    # Construct the sensor-frame measurement that corresponds to gravity PA=0:
    # invert the transform. gravity = rotate(sensor, sky_off) then rotate(-q).
    # So sensor = rotate(gravity, +q) then rotate(-sky_off).
    sky_off = sensor_pa_to_sky_pa(sensor_angle, conv)
    gravity_true = elongation_to_double_angle(0.5, 0.0)
    tmp = rotate_double_angle(gravity_true, q)
    vec_sensor = rotate_double_angle(tmp, -sky_off)

    out = sky_to_gravity_double_angle(vec_sensor, sensor_angle, q, conv)
    _, pa = double_angle_to_pa_mag(out)
    assert min(pa, 180 - pa) == pytest.approx(0.0, abs=1e-9)
    assert out.magnitude == pytest.approx(0.5)


def test_constant_sensor_offset_cancels_in_difference():
    # PRD 6.4a: the sensor angle subtracts out of the approach-difference
    # (flop) term within a run, but NOT out of the flexure field.
    conv = SensorAngleConvention()
    q = 15.0
    approach_a = elongation_to_double_angle(0.4, 10.0)
    approach_b = elongation_to_double_angle(0.4, 55.0)
    for sensor_angle in (0.0, 33.0, 90.0):
        ga = sky_to_gravity_double_angle(approach_a, sensor_angle, q, conv)
        gb = sky_to_gravity_double_angle(approach_b, sensor_angle, q, conv)
        diff = DoubleAngleVector(ga.x - gb.x, ga.y - gb.y)
        # The difference magnitude is invariant to the (common) sensor offset.
        ref_a = sky_to_gravity_double_angle(approach_a, 0.0, q, conv)
        ref_b = sky_to_gravity_double_angle(approach_b, 0.0, q, conv)
        ref = DoubleAngleVector(ref_a.x - ref_b.x, ref_a.y - ref_b.y)
        assert diff.magnitude == pytest.approx(ref.magnitude)
