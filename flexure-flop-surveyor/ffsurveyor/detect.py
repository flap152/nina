"""Per-frame star detection and elongation measurement (PRD 6.5 steps 1-3).

Detect stars, measure each one's second moments to get a semi-major/minor axis
and position angle, reject bad sources, then aggregate to a single robust
elongation vector in the **sensor frame**. The sensor -> gravity rotation is a
separate stage (see :mod:`ffsurveyor.angles` and :mod:`ffsurveyor.frame`).

Detection uses photutils image segmentation; each source's shape comes from its
intensity-weighted second central moments, which is exactly the (a, b, PA)
information the survey needs. Elongation magnitude defaults to eccentricity but
``(a-b)/(a+b)`` is also available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .angles import DoubleAngleVector, elongation_to_double_angle

__all__ = ["DetectionConfig", "StarMeasurements", "FrameElongation", "measure_frame"]


@dataclass
class DetectionConfig:
    """Tunables for star detection and source rejection."""

    detect_sigma: float = 5.0          # detection threshold in background sigma
    min_pixels: int = 5                # minimum connected pixels for a source
    border_px: int = 16                # reject sources whose centroid is within this of an edge
    saturation: Optional[float] = None # reject sources with any pixel >= this ADU (None = skip)
    min_snr: float = 10.0              # reject sources below this flux/err SNR
    max_ellipticity: float = 0.9       # reject implausibly elongated (blends/trails)
    magnitude_metric: str = "eccentricity"  # or "axis_ratio" for (a-b)/(a+b)
    max_stars: int = 500               # cap on brightest stars used (speed)


@dataclass
class StarMeasurements:
    """Per-star measurements retained after rejection (sensor frame)."""

    x: np.ndarray
    y: np.ndarray
    a: np.ndarray            # semi-major sigma (pixels)
    b: np.ndarray            # semi-minor sigma (pixels)
    pa_deg: np.ndarray       # orientation, deg CCW from +x (sensor frame)
    magnitude: np.ndarray    # elongation magnitude per chosen metric
    snr: np.ndarray

    def __len__(self) -> int:
        return len(self.x)


@dataclass
class FrameElongation:
    """The single robust elongation vector for a frame, in the sensor frame."""

    vector: DoubleAngleVector   # double-angle, sensor frame
    n_stars: int
    magnitude: float            # aggregate magnitude (|vector|)
    pa_deg: float               # aggregate PA (sensor frame), [0, 180)
    per_star: StarMeasurements


def _elongation_magnitude(a, b, metric: str):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if metric == "eccentricity":
        ratio = np.clip(b / a, 0.0, 1.0)
        return np.sqrt(1.0 - ratio ** 2)
    if metric == "axis_ratio":
        return (a - b) / (a + b)
    raise ValueError(f"unknown magnitude_metric: {metric!r}")


def measure_frame(image: np.ndarray, config: Optional[DetectionConfig] = None) -> FrameElongation:
    """Detect stars in ``image`` and return the aggregate sensor-frame elongation.

    Parameters
    ----------
    image : 2D ndarray
        Calibrated (or raw) science frame in ADU.
    config : DetectionConfig, optional

    Returns
    -------
    FrameElongation

    Raises
    ------
    ValueError
        If no usable stars survive rejection.
    """
    from astropy.stats import SigmaClip, sigma_clipped_stats
    from photutils.background import Background2D, MedianBackground
    from photutils.segmentation import SourceCatalog, detect_sources

    if config is None:
        config = DetectionConfig()

    image = np.asarray(image, dtype=float)

    # Background estimate + global noise sigma.
    try:
        bkg = Background2D(
            image,
            box_size=max(32, min(image.shape) // 8),
            filter_size=3,
            sigma_clip=SigmaClip(sigma=3.0),
            bkg_estimator=MedianBackground(),
        )
        data = image - bkg.background
        bkg_rms = float(np.median(bkg.background_rms))
    except Exception:
        # Fall back to a simple global estimate on small/degenerate frames.
        mean, median, std = sigma_clipped_stats(image, sigma=3.0)
        data = image - median
        bkg_rms = float(std)

    if bkg_rms <= 0 or not np.isfinite(bkg_rms):
        raise ValueError("could not estimate a positive background RMS")

    threshold = config.detect_sigma * bkg_rms
    segm = detect_sources(data, threshold, npixels=config.min_pixels)
    if segm is None:
        raise ValueError("no sources detected above threshold")

    cat = SourceCatalog(data, segm)

    def _col(attr, unit=None):
        v = getattr(cat, attr)
        v = getattr(v, "value", v) if unit is None else v.to(unit).value
        return np.atleast_1d(np.asarray(v, float))

    x = _col("xcentroid")
    y = _col("ycentroid")
    a = _col("semimajor_sigma")           # pixels
    b = _col("semiminor_sigma")           # pixels
    orient = _col("orientation", "deg")   # CCW from +x
    flux = _col("segment_flux")
    npix = _col("area")
    peak = _col("max_value")

    # Per-source noise: shot-free approximation from background over the aperture.
    err = bkg_rms * np.sqrt(np.maximum(npix, 1.0))
    snr = np.divide(flux, err, out=np.zeros_like(flux), where=err > 0)

    ny, nx = data.shape
    ecc_full = _elongation_magnitude(a, b, "eccentricity")  # for the ellipticity cut

    keep = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
    keep &= x >= config.border_px
    keep &= y >= config.border_px
    keep &= x <= (nx - 1 - config.border_px)
    keep &= y <= (ny - 1 - config.border_px)
    keep &= snr >= config.min_snr
    keep &= ecc_full <= config.max_ellipticity
    if config.saturation is not None:
        keep &= peak < config.saturation

    idx = np.nonzero(keep)[0]
    if idx.size == 0:
        raise ValueError("no stars survived rejection cuts")

    # Keep the brightest up to max_stars.
    if idx.size > config.max_stars:
        order = np.argsort(flux[idx])[::-1][: config.max_stars]
        idx = idx[order]

    mag = _elongation_magnitude(a[idx], b[idx], config.magnitude_metric)
    stars = StarMeasurements(
        x=x[idx], y=y[idx], a=a[idx], b=b[idx],
        pa_deg=orient[idx], magnitude=mag, snr=snr[idx],
    )

    # Aggregate in double-angle space, SNR-weighted, sigma-clipped robust mean.
    vecs = elongation_to_double_angle(stars.magnitude, stars.pa_deg)
    w = stars.snr
    agg = _robust_weighted_mean(vecs, w)

    return FrameElongation(
        vector=agg,
        n_stars=int(idx.size),
        magnitude=float(agg.magnitude),
        pa_deg=float(agg.pa_deg),
        per_star=stars,
    )


def _robust_weighted_mean(vecs: DoubleAngleVector, weights, n_sigma: float = 3.0) -> DoubleAngleVector:
    """Sigma-clipped, weight-aware mean of double-angle vectors."""
    x = np.atleast_1d(vecs.x).astype(float)
    y = np.atleast_1d(vecs.y).astype(float)
    w = np.atleast_1d(np.asarray(weights, float))
    w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
    if w.sum() == 0:
        w = np.ones_like(x)

    mx = np.average(x, weights=w)
    my = np.average(y, weights=w)
    if x.size >= 4:
        d = np.hypot(x - mx, y - my)
        scale = np.median(d) if np.median(d) > 0 else d.std()
        if scale > 0:
            good = d <= n_sigma * scale * 1.4826 + 1e-12
            if good.sum() >= max(3, x.size // 2):
                mx = np.average(x[good], weights=w[good])
                my = np.average(y[good], weights=w[good])
    return DoubleAngleVector(np.asarray(mx), np.asarray(my))
