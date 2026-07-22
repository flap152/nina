"""End-to-end pipeline test on synthetic FITS with real WCS headers.

Exercises the full analysis stack — FITS IO, header-WCS solve, astropy
parallactic angle, detection, gravity-frame rotation, node aggregation, and the
run comparison — and asserts the physically meaningful behaviors rather than
re-deriving WCS sign conventions by hand (PRD 9).
"""

import os

import numpy as np
import pytest

from ffsurveyor.config import GuideConfig, RunManifest, Site
from ffsurveyor.aggregate import aggregate_nodes
from ffsurveyor.compare import compare_runs
from ffsurveyor.ingest import ingest_run


def _elliptical_gaussian(shape, x0, y0, sig_a, sig_b, pa_deg, amp):
    ny, nx = shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    t = np.radians(pa_deg)
    dx, dy = xx - x0, yy - y0
    xr = dx * np.cos(t) + dy * np.sin(t)
    yr = -dx * np.sin(t) + dy * np.cos(t)
    return amp * np.exp(-0.5 * ((xr / sig_a) ** 2 + (yr / sig_b) ** 2))


def _write_frame(path, sensor_pa, ra_deg, dec_deg, date_obs,
                 elong=(3.4, 2.0), n_stars=40, shape=(400, 400), scale_arcsec=1.0, seed=0):
    from astropy.io import fits

    rng = np.random.default_rng(seed)
    img = np.full(shape, 200.0)
    margin = 40
    for _ in range(n_stars):
        x0 = rng.uniform(margin, shape[1] - margin)
        y0 = rng.uniform(margin, shape[0] - margin)
        amp = rng.uniform(4000, 12000)
        img += _elliptical_gaussian(img.shape, x0, y0, elong[0], elong[1], sensor_pa, amp)
    img += rng.normal(0.0, 10.0, size=shape)

    hdu = fits.PrimaryHDU(img.astype(np.float32))
    h = hdu.header
    s = scale_arcsec / 3600.0
    h["CTYPE1"] = "RA---TAN"
    h["CTYPE2"] = "DEC--TAN"
    h["CUNIT1"] = "deg"
    h["CUNIT2"] = "deg"
    h["CRPIX1"] = shape[1] / 2.0
    h["CRPIX2"] = shape[0] / 2.0
    h["CRVAL1"] = ra_deg
    h["CRVAL2"] = dec_deg
    h["CD1_1"] = -s
    h["CD1_2"] = 0.0
    h["CD2_1"] = 0.0
    h["CD2_2"] = s
    h["DATE-OBS"] = date_obs
    hdu.writeto(path, overwrite=True)


def _manifest(guide_id="GS", mode="guidescope"):
    return RunManifest(
        run_id="test",
        date="2026-01-15",
        site=Site(latitude_deg=34.0, longitude_deg=-118.0, elevation_m=100.0),
        guide=GuideConfig(config_id=guide_id, mode=mode),
        prefer_wcs_angle=True,
    )


def _write_sidecar(path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "node_id", "approach", "burst"])
        w.writeheader()
        w.writerows(rows)


def _build_run(tmpdir, node_specs, elong=(3.4, 2.0), tag=""):
    """node_specs: list of (node_id, ra, dec, {approach: sensor_pa}). 3 frames each."""
    rows = []
    date = "2026-01-15T09:00:00"
    fi = 0
    for node_id, ra, dec, appr_pa in node_specs:
        for approach, spa in appr_pa.items():
            for k in range(3):
                fname = f"{tag}f{fi:03d}.fits"
                _write_frame(os.path.join(tmpdir, fname), spa, ra, dec, date,
                             elong=elong, seed=fi + 1)
                rows.append({"filename": fname, "node_id": node_id,
                             "approach": approach, "burst": "post_settle"})
                fi += 1
    _write_sidecar(os.path.join(tmpdir, f"{tag}sidecar.csv"), rows)
    return os.path.join(tmpdir, f"{tag}*.fits"), os.path.join(tmpdir, f"{tag}sidecar.csv")


def test_solve_from_header_recovers_pointing(tmp_path):
    from astropy.io import fits
    from ffsurveyor.solve import solve_from_header

    p = str(tmp_path / "one.fits")
    _write_frame(p, 20.0, 150.0, 40.0, "2026-01-15T09:00:00")
    with fits.open(p) as hdul:
        sol = solve_from_header(hdul[0].header, data_shape=hdul[0].data.shape)
    assert sol is not None
    assert sol.ra_deg == pytest.approx(150.0, abs=0.05)
    assert sol.dec_deg == pytest.approx(40.0, abs=0.05)
    assert sol.pixscale_arcsec == pytest.approx(1.0, abs=0.02)


def test_no_hysteresis_when_approaches_identical(tmp_path):
    d = str(tmp_path)
    specs = [("N1", 150.0, 40.0, {"high": 25.0, "low": 25.0})]  # same sensor PA
    fits_glob, sidecar = _build_run(d, specs)
    rep = ingest_run(_manifest(), fits_glob, sidecar)
    assert len(rep.processed) == 6
    nodes = aggregate_nodes(rep.processed)
    assert len(nodes) == 1
    assert nodes[0].hysteresis_mag < 0.06  # ~0 up to detection noise
    assert nodes[0].flexure_mag > 0.4      # clearly elongated


def test_hysteresis_appears_when_approaches_differ(tmp_path):
    d = str(tmp_path)
    specs = [("N1", 150.0, 40.0, {"high": 10.0, "low": 70.0})]  # 60 deg apart
    fits_glob, sidecar = _build_run(d, specs)
    rep = ingest_run(_manifest(), fits_glob, sidecar)
    nodes = aggregate_nodes(rep.processed)
    assert nodes[0].hysteresis_mag > 0.3   # large path-dependent signal


def test_repeatability_low_for_clean_frames(tmp_path):
    d = str(tmp_path)
    specs = [("N1", 150.0, 40.0, {"high": 30.0, "low": 30.0})]
    fits_glob, sidecar = _build_run(d, specs)
    rep = ingest_run(_manifest(), fits_glob, sidecar)
    nodes = aggregate_nodes(rep.processed)
    assert nodes[0].mean_repeatability < 0.1


def test_compare_runs_detects_flexure_reduction(tmp_path):
    d = str(tmp_path)
    specs_a = [("N1", 150.0, 40.0, {"high": 20.0, "low": 20.0}),
               ("N2", 160.0, 30.0, {"high": 20.0, "low": 20.0})]
    # Run B: rounder stars => smaller flexure magnitude (the "OAG helps" case).
    fa, sa = _build_run(d, specs_a, elong=(3.6, 2.0), tag="A")
    fb, sb = _build_run(d, specs_a, elong=(2.7, 2.4), tag="B")
    rep_a = ingest_run(_manifest("GS", "guidescope"), fa, sa)
    rep_b = ingest_run(_manifest("OAG", "oag"), fb, sb)
    nodes_a = aggregate_nodes(rep_a.processed)
    nodes_b = aggregate_nodes(rep_b.processed)
    comp = compare_runs(nodes_a, nodes_b, "Guidescope", "OAG")
    assert len(comp.nodes) == 2
    assert comp.mean_flexure_a > comp.mean_flexure_b
    assert comp.mean_flexure_reduction > 0.1
