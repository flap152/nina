"""Contract tests: run representative NINA JSON through the real client parsers.

These exercise the transport envelope + field parsing (pier-side key spellings,
RA hours-vs-degrees, PlateSolveResult key variants) without a network, so the
parsers are validated against plausible real shapes rather than only the fake's
assumed one. Replace the payloads with responses recorded from a live NINA/sim
to turn these into true regression fixtures.
"""

import json

import pytest

from ffsurveyor.capture.ninaapi import NinaApiError, NinaClient


class FakeOpener:
    """Returns a canned JSON body for any request."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def open(self, req, timeout=None):
        body = self._body

        class _Resp:
            def read(self_inner):
                return body

        return _Resp()


def _client(payload):
    return NinaClient(opener=FakeOpener(payload))


def _envelope(response):
    return {"Response": response, "Success": True, "StatusCode": 200, "Type": "API"}


@pytest.mark.parametrize("key", ["SideOfPier", "PierSide", "sideOfPier"])
def test_mount_info_pierside_key_variants(key):
    c = _client(_envelope({key: "East", "Declination": 40.0, "RightAscension": 10.0}))
    info = c.mount_info()
    assert info.pier_side == "East"


def test_mount_info_ra_hours_converted_to_degrees():
    # RA reported in hours (0-24) is converted to degrees.
    c = _client(_envelope({"RightAscension": 10.0, "Declination": 40.0, "PierSide": "West"}))
    info = c.mount_info()
    assert info.ra_deg == pytest.approx(150.0)
    assert info.dec_deg == pytest.approx(40.0)


def test_mount_info_ra_already_degrees_passthrough():
    # A value > 24 can only be degrees; left as-is.
    c = _client(_envelope({"RADeg": 200.0, "DecDeg": -12.0}))
    info = c.mount_info()
    assert info.ra_deg == pytest.approx(200.0)


def test_success_false_raises():
    c = _client({"Success": False, "Error": "camera not connected", "StatusCode": 500})
    with pytest.raises(NinaApiError):
        c.mount_info()


@pytest.mark.parametrize("rot_key", ["Rotation", "PositionAngle", "Orientation"])
def test_extract_solve_rotation_key_variants(rot_key):
    resp = {"PlateSolveResult": {rot_key: 37.4, "Ra": 10.0, "Dec": 40.0, "PixelScale": 0.98}}
    readout = NinaClient.extract_solve(resp)
    assert readout.rotation_deg == pytest.approx(37.4)
    assert readout.ra_deg == pytest.approx(150.0)   # hours -> deg
    assert readout.pixel_scale == pytest.approx(0.98)


def test_extract_solve_missing_result_is_none():
    readout = NinaClient.extract_solve({"SomethingElse": 1})
    assert readout.rotation_deg is None
    assert readout.raw == {}


def test_capture_returns_dict_with_solve_result():
    payload = _envelope({"PlateSolveResult": {"Rotation": 5.0}, "SavedPath": "C:/x.fits"})
    c = _client(payload)
    resp = c.capture(3.0, solve=True)
    assert NinaClient.extract_solve(resp).rotation_deg == pytest.approx(5.0)
