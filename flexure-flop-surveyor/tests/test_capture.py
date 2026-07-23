"""Tests for the live-orchestration invariants (PRD points 2-6).

A fake client records the exact call sequence so we can assert the runner never
centers, always does offset-then-slew-in, enforces same pier side, and captures
immediate + post-settle bursts around the settle.
"""

import pytest

from ffsurveyor.capture.ninaapi import NinaClient
from ffsurveyor.capture.planner import build_survey_plan
from ffsurveyor.capture.runner import RunnerConfig, SurveyRunner
from ffsurveyor.config import GuideConfig, RunManifest, Site


class FakeClient:
    """Records every call; exposes only raw slew / info / capture (no center)."""

    def __init__(self, pier="West", rotation=15.0, pier_by_leg=None):
        self.calls = []          # shared event log, in order
        self.pier = pier
        self.pier_by_leg = pier_by_leg or {}
        self.rotation = rotation
        self._leg_counter = 0

    def set_tracking(self, mode="Sidereal"):
        self.calls.append(("set_tracking", mode))

    def slew_radec(self, ra, dec, wait=True):
        self.calls.append(("slew", round(ra, 4), round(dec, 4)))

    def mount_info(self):
        from ffsurveyor.capture.ninaapi import MountInfo
        self.calls.append(("mount_info",))
        return MountInfo(pier_side=self.pier, ra_deg=None, dec_deg=None,
                         tracking=True, raw={})

    def capture(self, exposure_s, *, gain=None, binning=None, filter_name=None,
                save=True, filename=None, solve=False, solve_timeout_s=None, wait=True):
        self.calls.append(("capture", filename, ("solve" if solve else "nosolve")))
        return {"PlateSolveResult": {"Rotation": self.rotation, "Ra": 10.0, "Dec": 40.0}}

    def start_guiding(self, calibrate=False, wait=True):
        self.calls.append(("start_guiding", "calibrate" if calibrate else "resume"))

    def stop_guiding(self):
        self.calls.append(("stop_guiding",))

    def guider_info(self):
        self.calls.append(("guider_info",))
        return {"RMSTotal": 0.42}

    extract_solve = staticmethod(NinaClient.extract_solve)
    extract_guide_rms = staticmethod(NinaClient.extract_guide_rms)


def _manifest():
    return RunManifest(
        run_id="t", date="2026-01-15",
        site=Site(latitude_deg=34.0, longitude_deg=-118.0, elevation_m=100.0),
        guide=GuideConfig(config_id="GS", mode="guidescope"),
    )


def _single_node_plan():
    # One node, altitude-axis, two approaches.
    plan = build_survey_plan(alt_bands_deg=(50.0,), azimuths_deg=(120.0,),
                             load_axis="altitude", offset_deg=8.0)
    assert len(plan) == 1
    assert len(plan.nodes[0].approaches) == 2
    return plan


def _run(client, config=None):
    cfg = config or RunnerConfig(burst_count=2, settle_s=3.0)
    runner = SurveyRunner(client, _manifest(), _single_node_plan(), cfg,
                          clock=lambda: "2026-01-15T09:00:00", sleep=lambda s: client.calls.append(("sleep", s)))
    runner.run()
    return runner


def test_never_centers_only_raw_slews():
    client = FakeClient()
    _run(client)
    # The client exposes no center method and none is invoked.
    assert not hasattr(client, "center")
    slews = [c for c in client.calls if c[0] == "slew"]
    assert len(slews) == 4  # 2 approaches x (offset + node)


def test_offset_then_slew_in_order():
    client = FakeClient()
    runner = _run(client)
    # For each approach: an offset slew, THEN the node slew (same node coords).
    node = runner.plan.nodes[0]
    # Node RA/Dec resolved at the fixed clock:
    node_ra, node_dec = runner._altaz_to_radec(node.alt_deg, node.az_deg, "2026-01-15T09:00:00")
    slews = [c for c in client.calls if c[0] == "slew"]
    # Legs are consecutive pairs; the SECOND slew of each pair is the node.
    for pair_start in (0, 2):
        offset = slews[pair_start]
        arrive = slews[pair_start + 1]
        assert arrive[1] == pytest.approx(round(node_ra, 4), abs=1e-3)
        assert arrive[2] == pytest.approx(round(node_dec, 4), abs=1e-3)
        assert (offset[1], offset[2]) != (arrive[1], arrive[2])  # offset differs


def test_immediate_burst_before_settle_then_post_settle():
    client = FakeClient()
    _run(client, RunnerConfig(burst_count=2, settle_s=4.0))
    # Look at the first approach's slice: ... capture(immediate)x2, sleep, capture(post_settle)x2
    labels = [c for c in client.calls]
    # find first sleep
    sleep_idx = next(i for i, c in enumerate(labels) if c[0] == "sleep")
    before = [c for c in labels[:sleep_idx] if c[0] == "capture"]
    after = [c for c in labels[sleep_idx + 1:] if c[0] == "capture"]
    assert all("immediate" in c[1] for c in before[-2:])   # immediate precedes settle
    assert any("post_settle" in c[1] for c in after)       # post-settle follows


def test_solve_requested_for_angle_readout_only_first_frame():
    client = FakeClient()
    _run(client, RunnerConfig(burst_count=3, solve_first_of_burst_only=True))
    caps = [c for c in client.calls if c[0] == "capture"]
    solved = [c for c in caps if c[2] == "solve"]
    # One solve per burst; 2 approaches x 2 bursts = 4 solves, rest nosolve.
    assert len(solved) == 4
    assert len(caps) == 2 * 2 * 3  # approaches x bursts x burst_count


def test_records_nina_rotation_crosscheck():
    client = FakeClient(rotation=42.5)
    runner = _run(client)
    solved_rows = [r for r in runner.sidecar_rows if r["nina_rotation_deg"] is not None]
    assert solved_rows and all(r["nina_rotation_deg"] == 42.5 for r in solved_rows)


def test_same_pier_side_ok_no_warning():
    client = FakeClient(pier="West")
    runner = _run(client)
    assert all(not v.warnings for v in runner.visits)


def test_pier_side_mismatch_flagged():
    # Second approach reports a different pier side -> warning recorded.
    class FlipClient(FakeClient):
        def mount_info(self):
            from ffsurveyor.capture.ninaapi import MountInfo
            self.calls.append(("mount_info",))
            self._leg_counter += 1
            side = "West" if self._leg_counter == 1 else "East"
            return MountInfo(pier_side=side, ra_deg=None, dec_deg=None, tracking=True, raw={})

    client = FlipClient()
    runner = _run(client)
    assert any(v.warnings for v in runner.visits)


def _run_guided(client, config=None):
    cfg = config or RunnerConfig(burst_count=2, settle_s=1.0, guiding=True, guide_settle_s=1.0)
    runner = SurveyRunner(client, _manifest(), _single_node_plan(), cfg,
                          clock=lambda: "2026-01-15T09:00:00",
                          sleep=lambda s: client.calls.append(("sleep", s)))
    runner.run()
    return runner


def test_guiding_resumes_after_each_slew_never_recalibrates():
    client = FakeClient()
    _run_guided(client)
    starts = [c for c in client.calls if c[0] == "start_guiding"]
    # One resume per approach (2), plus the optional start-of-run call.
    assert len(starts) >= 2
    # Mid-survey resumes must NEVER recalibrate (calibrate=False).
    # calibrate_at_start defaults False, so NO call should recalibrate at all here.
    assert all(s[1] == "resume" for s in starts), starts


def test_guiding_records_guide_rms_per_frame():
    client = FakeClient()
    runner = _run_guided(client)
    assert all(r["guide_rms"] == 0.42 for r in runner.sidecar_rows)
    assert all(v.guide_rms == 0.42 for v in runner.visits)


def test_calibrate_at_start_allows_exactly_one_calibration():
    client = FakeClient()
    _run_guided(client, RunnerConfig(burst_count=1, guiding=True, guide_settle_s=0.0,
                                     calibrate_at_start=True))
    starts = [c for c in client.calls if c[0] == "start_guiding"]
    calibrations = [s for s in starts if s[1] == "calibrate"]
    assert len(calibrations) == 1  # only the run-start call calibrates


def test_no_guiding_calls_when_guiding_off():
    client = FakeClient()
    _run(client)  # default config: guiding off
    assert not [c for c in client.calls if c[0] in ("start_guiding", "guider_info")]


def test_planner_drops_node_without_feasible_pair_near_horizon():
    # At alt=22 with an 8 deg offset and min_altitude 20, the "low" offset (14)
    # is below the cutoff, so no opposite-sense pair exists -> node dropped.
    plan = build_survey_plan(alt_bands_deg=(22.0,), azimuths_deg=(90.0,),
                             load_axis="altitude", offset_deg=8.0,
                             min_altitude_deg=20.0)
    assert len(plan) == 0


def test_planner_zenith_guard_skips_high_nodes():
    plan = build_survey_plan(alt_bands_deg=(88.0,), azimuths_deg=(0.0,),
                             zenith_guard_deg=85.0)
    assert len(plan) == 0


def test_planner_dec_axis_keeps_both_approaches():
    plan = build_survey_plan(alt_bands_deg=(30.0,), azimuths_deg=(90.0,),
                             load_axis="dec", offset_deg=10.0)
    assert len(plan) == 1
    tags = {leg.tag for leg in plan.nodes[0].approaches}
    assert tags == {"pdec", "mdec"}


def test_strict_pier_side_aborts_capture():
    class FlipClient(FakeClient):
        def mount_info(self):
            from ffsurveyor.capture.ninaapi import MountInfo
            self.calls.append(("mount_info",))
            self._leg_counter += 1
            side = "West" if self._leg_counter == 1 else "East"
            return MountInfo(pier_side=side, ra_deg=None, dec_deg=None, tracking=True, raw={})

    client = FlipClient()
    runner = _run(client, RunnerConfig(burst_count=2, strict_pier_side=True))
    # The mismatched second approach captured nothing.
    second = runner.visits[1]
    assert second.warnings and second.frames == []
