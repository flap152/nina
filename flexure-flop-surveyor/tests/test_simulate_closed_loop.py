"""Closed-loop test: inject a known field, analyze, confirm recovery.

This is the strongest single check that the science works end to end — the
gravity-frame separation of flexure vs flop is validated against ground truth the
generator injected, with no NINA in the loop.
"""

import numpy as np
import pytest

from ffsurveyor.aggregate import aggregate_nodes
from ffsurveyor.config import GuideConfig, RunManifest, Site
from ffsurveyor.ingest import ingest_run
from ffsurveyor.simulate import InjectionModel, simulate_run


def _manifest():
    return RunManifest(
        run_id="sim", date="2026-01-15",
        site=Site(latitude_deg=34.0, longitude_deg=-118.0, elevation_m=100.0),
        guide=GuideConfig(config_id="sim", mode="guidescope"),
    )


def _analyze(out_dir):
    m = _manifest()
    rep = ingest_run(m, f"{out_dir}/*.fits", f"{out_dir}/sidecar.csv")
    assert not rep.skipped, rep.skipped
    return aggregate_nodes(rep.processed)


def test_recovers_flexure_direction_and_altitude_trend(tmp_path):
    out = str(tmp_path)
    model = InjectionModel(flexure_amp=0.6, flop_amp=0.15, seeing_sigma=0.02)
    simulate_run(out, _manifest(), model=model, burst_count=3)
    nodes = {n.node_id: n for n in _analyze(out)}

    # Flexure points along the local vertical => gravity-frame PA ~ 0 (or 180).
    for n in nodes.values():
        fold = min(n.flexure_pa_deg, 180 - n.flexure_pa_deg)
        assert fold < 10.0, f"{n.node_id} flexure PA {n.flexure_pa_deg}"

    # Flexure is worse near the horizon: alt 30 > alt 60.
    lo = np.mean([n.flexure_mag for k, n in nodes.items() if k.startswith("Alt30")])
    hi = np.mean([n.flexure_mag for k, n in nodes.items() if k.startswith("Alt60")])
    assert lo > hi + 0.1


def test_recovers_hysteresis_magnitude(tmp_path):
    out = str(tmp_path)
    model = InjectionModel(flexure_amp=0.5, flop_amp=0.15, seeing_sigma=0.02)
    simulate_run(out, _manifest(), model=model, burst_count=3)
    nodes = _analyze(out)
    # Injected approach difference is 2 * flop_amp = 0.30.
    for n in nodes:
        assert n.hysteresis_mag == pytest.approx(0.30, abs=0.07), n.node_id
        assert n.hysteresis_mag > n.mean_repeatability  # deterministic, not noise


def test_zero_flop_gives_no_hysteresis(tmp_path):
    out = str(tmp_path)
    # More stars + frames drive down the hysteresis noise floor, which scales
    # with flexure magnitude (a fixed angular scatter -> larger vector diff at
    # high |flexure|). With no flop injected, hysteresis should be a small noise
    # residual, not a deterministic signal like the 0.30 of the flop test.
    model = InjectionModel(flexure_amp=0.5, flop_amp=0.0, seeing_sigma=0.015, n_stars=60)
    simulate_run(out, _manifest(), model=model, burst_count=6)
    nodes = _analyze(out)
    for n in nodes:
        # No deterministic approach difference: the between-approach hysteresis is
        # no larger than the within-burst noise floor (unlike a real flop, which
        # sits well ABOVE repeatability -- see test_recovers_hysteresis_magnitude).
        assert n.hysteresis_mag < 1.5 * n.mean_repeatability, \
            f"{n.node_id}: hyst {n.hysteresis_mag:.3f} vs rep {n.mean_repeatability:.3f}"
