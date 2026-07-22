"""Opt-in integration test against a real NINA Advanced API.

Skipped unless ``FFS_NINA_HOST`` is set (e.g. to the LAN IP of the Win11 mount
PC running NINA + the Advanced API plugin, ideally with ASCOM simulator devices).
Read-only by default: it runs ``ffsurvey doctor``'s non-hardware checks.

Example::

    FFS_NINA_HOST=192.168.1.50 FFS_NINA_PORT=1888 pytest tests/test_integration_live.py -q
"""

import os

import pytest

HOST = os.environ.get("FFS_NINA_HOST")
PORT = int(os.environ.get("FFS_NINA_PORT", "1888"))
API_KEY = os.environ.get("FFS_NINA_APIKEY") or None

pytestmark = pytest.mark.skipif(not HOST, reason="set FFS_NINA_HOST to run live integration test")


def test_live_doctor_readonly():
    from ffsurveyor.capture import NinaClient, run_doctor

    client = NinaClient(host=HOST, port=PORT, api_key=API_KEY, timeout_s=30.0)
    report = run_doctor(client)  # read-only checks only
    print("\n" + report.render())
    # api-reachable must pass; mount/camera may warn if simulators aren't connected.
    statuses = {c.name: c.status for c in report.checks}
    assert statuses.get("api-reachable") == "ok", report.render()
