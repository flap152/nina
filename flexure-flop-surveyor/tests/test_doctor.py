"""Tests for the ``ffsurvey doctor`` preflight logic (fake clients)."""

import pytest

from ffsurveyor.capture.doctor import FAIL, OK, UNKNOWN, WARN, run_doctor
from ffsurveyor.capture.ninaapi import MountInfo, NinaClient


class BaseClient:
    def __init__(self, pier="West", ra=150.0, dec=40.0, rotation=20.0):
        self.pier, self.ra, self.dec, self.rotation = pier, ra, dec, rotation

    def version(self):
        return "2.2.0.0"

    def mount_info(self):
        return MountInfo(pier_side=self.pier, ra_deg=self.ra, dec_deg=self.dec,
                         tracking=True, raw={})

    def _get(self, endpoint, params=None):
        return {"Connected": True}

    def capture(self, exposure_s, **kw):
        return {"PlateSolveResult": {"Rotation": self.rotation}}

    def slew_radec(self, ra, dec, wait=True):
        pass

    extract_solve = staticmethod(NinaClient.extract_solve)


def _status(report, name):
    return next(c.status for c in report.checks if c.name == name)


def test_unreachable_fails_fast():
    class Dead(BaseClient):
        def version(self):
            raise RuntimeError("connection refused")

    rep = run_doctor(Dead())
    assert _status(rep, "api-reachable") == FAIL
    assert not rep.ok
    assert len(rep.checks) == 1  # short-circuits


def test_readonly_all_ok():
    rep = run_doctor(BaseClient())
    assert _status(rep, "api-reachable") == OK
    assert _status(rep, "mount-pier-side") == OK
    assert rep.ok


def test_missing_pierside_warns_not_fails():
    class NoPier(BaseClient):
        def mount_info(self):
            return MountInfo(pier_side=None, ra_deg=self.ra, dec_deg=self.dec,
                             tracking=True, raw={})

    rep = run_doctor(NoPier())
    assert _status(rep, "mount-pier-side") == WARN
    assert rep.ok  # warn is not blocking


def test_capture_test_detects_sync():
    # Pointing changes across the solve -> capture-solve is syncing (blocking fail).
    class Syncing(BaseClient):
        def __init__(self):
            super().__init__()
            self._captured = False

        def capture(self, exposure_s, **kw):
            self._captured = True
            return super().capture(exposure_s, **kw)

        def mount_info(self):
            dec = self.dec + (5.0 if self._captured else 0.0)  # moves once solved
            return MountInfo(pier_side=self.pier, ra_deg=self.ra, dec_deg=dec,
                             tracking=True, raw={})

    rep = run_doctor(Syncing(), capture_test=True)
    assert _status(rep, "solve-no-sync") == FAIL
    assert not rep.ok


def test_capture_test_no_sync_ok():
    rep = run_doctor(BaseClient(), capture_test=True)
    assert _status(rep, "capture-solve") == OK
    assert _status(rep, "solve-no-sync") == OK


def test_slew_test_ok_when_no_move():
    rep = run_doctor(BaseClient(), slew_test=True)
    assert _status(rep, "ra-unit") == OK


def test_slew_test_flags_ra_unit_mismatch():
    # Mount ends far from the commanded position -> RA-unit suspicion.
    class Drifts(BaseClient):
        def __init__(self):
            super().__init__()
            self._slewed = False

        def slew_radec(self, ra, dec, wait=True):
            self._slewed = True  # a "no-op" slew that actually moves => wrong RA unit

        def mount_info(self):
            ra = self.ra + (30.0 if self._slewed else 0.0)
            return MountInfo(pier_side=self.pier, ra_deg=ra, dec_deg=self.dec,
                             tracking=True, raw={})

    rep = run_doctor(Drifts(), slew_test=True)
    assert _status(rep, "ra-unit") == FAIL
