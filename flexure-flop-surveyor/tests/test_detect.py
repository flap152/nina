"""Synthetic end-to-end test for the detection + elongation stage.

Generate a field of identical elliptical Gaussian "stars" at a known sensor
position angle, then confirm :func:`measure_frame` recovers that PA and a
non-zero magnitude after detection, moment measurement, and robust aggregation.
"""

import numpy as np
import pytest

from ffsurveyor.detect import DetectionConfig, measure_frame


def _elliptical_gaussian(shape, x0, y0, sig_a, sig_b, pa_deg, amp):
    ny, nx = shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    t = np.radians(pa_deg)
    dx = xx - x0
    dy = yy - y0
    # Rotate into the ellipse's principal axes (major along PA).
    xr = dx * np.cos(t) + dy * np.sin(t)
    yr = -dx * np.sin(t) + dy * np.cos(t)
    return amp * np.exp(-0.5 * ((xr / sig_a) ** 2 + (yr / sig_b) ** 2))


def _synthetic_field(pa_deg, sig_a=3.2, sig_b=2.0, n_stars=40, shape=(512, 512), seed=0):
    rng = np.random.default_rng(seed)
    img = np.full(shape, 200.0)  # bias/background level
    margin = 40
    for _ in range(n_stars):
        x0 = rng.uniform(margin, shape[1] - margin)
        y0 = rng.uniform(margin, shape[0] - margin)
        amp = rng.uniform(3000, 12000)
        img += _elliptical_gaussian(shape, x0, y0, sig_a, sig_b, pa_deg, amp)
    img += rng.normal(0.0, 12.0, size=shape)  # read noise
    return img


@pytest.mark.parametrize("pa", [0.0, 30.0, 60.0, 120.0, 150.0])
def test_recovers_sensor_pa(pa):
    img = _synthetic_field(pa, seed=int(pa) + 1)
    result = measure_frame(img, DetectionConfig(min_snr=5.0))
    assert result.n_stars >= 15
    # PA folded to [0,180); compare on the circle (mod 180).
    diff = (result.pa_deg - pa + 90.0) % 180.0 - 90.0
    assert abs(diff) < 4.0
    assert result.magnitude > 0.4  # sig_a/sig_b = 1.6 -> clearly elongated


def test_round_stars_give_low_magnitude():
    # Truly circular stars (equal sigmas): aggregate magnitude near the noise floor.
    img = _synthetic_field(0.0, sig_a=2.6, sig_b=2.6, seed=99)
    result = measure_frame(img, DetectionConfig(min_snr=5.0))
    assert result.magnitude < 0.15


def test_raises_without_stars():
    rng = np.random.default_rng(3)
    flat = np.full((128, 128), 200.0) + rng.normal(0, 10, (128, 128))
    with pytest.raises(ValueError):
        measure_frame(flat, DetectionConfig())
