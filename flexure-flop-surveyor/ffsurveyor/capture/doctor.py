"""Preflight checks against a live NINA Advanced API (PRD 8, 9 verify-items).

``run_doctor`` turns the manual "confirm against a live instance" checklist into
one call. Default checks are **read-only** (version, mount info, camera info).
Two opt-in probes touch hardware and are off by default:

* ``capture_test`` — takes one exposure with ``solve=True`` and checks that the
  mount pointing did **not** change across the solve (i.e. capture-solve is not
  syncing/reslewing — PRD point 3), and reports the returned rotation angle.
* ``slew_test`` — slews to the mount's *current* RA/Dec. If the RA unit is right,
  nothing moves; if it is wrong (hours vs degrees), the mount will slew away. Use
  only when you can watch the mount.

The logic is pure and testable with a fake client; the CLI wires it to a real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

__all__ = ["Check", "DoctorReport", "run_doctor"]

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"


@dataclass
class Check:
    name: str
    status: str          # ok | warn | fail | unknown
    detail: str = ""


@dataclass
class DoctorReport:
    checks: List[Check] = field(default_factory=list)

    def add(self, name, status, detail=""):
        self.checks.append(Check(name, status, detail))

    @property
    def ok(self) -> bool:
        return all(c.status != FAIL for c in self.checks)

    def render(self) -> str:
        glyph = {OK: "PASS", WARN: "WARN", FAIL: "FAIL", UNKNOWN: "????"}
        lines = [f"[{glyph[c.status]}] {c.name}: {c.detail}" for c in self.checks]
        lines.append("")
        lines.append("Overall: " + ("no blocking failures" if self.ok else "BLOCKING FAILURES"))
        return "\n".join(lines)


def run_doctor(client, *, capture_test: bool = False, slew_test: bool = False,
               exposure_s: float = 3.0, solve_timeout_s: int = 60) -> DoctorReport:
    rep = DoctorReport()

    # 1. API reachable + version.
    try:
        ver = client.version()
        rep.add("api-reachable", OK, f"version={ver}")
    except Exception as exc:
        rep.add("api-reachable", FAIL, f"cannot reach NINA API: {exc}")
        return rep  # nothing else will work

    # 2. Mount info + pier-side field discovery.
    try:
        info = client.mount_info()
        if info.pier_side is not None:
            rep.add("mount-pier-side", OK, f"pier side reported: {info.pier_side}")
        else:
            rep.add("mount-pier-side", WARN,
                    "no pier-side field recognized in mount/info; "
                    "check the key name against NinaClient._first spellings")
        if info.ra_deg is not None:
            rep.add("mount-coords", OK, f"RA={info.ra_deg:.3f} deg, Dec={info.dec_deg:.3f} deg")
        else:
            rep.add("mount-coords", WARN, "mount/info did not expose current RA/Dec")
    except Exception as exc:
        rep.add("mount-info", FAIL, f"mount/info failed (is a mount connected?): {exc}")
        info = None

    # 3. Camera reachable (best-effort; info route may differ).
    try:
        cam = client._get("equipment/camera/info")
        rep.add("camera-info", OK, "camera/info responded")
    except Exception as exc:
        rep.add("camera-info", WARN, f"camera/info failed (is a camera connected?): {exc}")

    # 4. (opt-in) capture-solve is read-only.
    if capture_test:
        before = _safe_coords(client)
        try:
            resp = client.capture(exposure_s, save=False, solve=True,
                                  solve_timeout_s=solve_timeout_s, wait=True)
            readout = client.extract_solve(resp)
            if readout.rotation_deg is not None:
                rep.add("capture-solve", OK,
                        f"solve returned rotation={readout.rotation_deg:.2f} deg "
                        f"(confirm the sign convention manually, PRD 9)")
            else:
                rep.add("capture-solve", WARN,
                        "capture succeeded but no rotation in PlateSolveResult; "
                        "check star field / solver settings")
        except Exception as exc:
            rep.add("capture-solve", FAIL, f"capture(solve=True) failed: {exc}")
            resp = None
        after = _safe_coords(client)
        if before and after:
            moved = _angular_sep_deg(before, after)
            if moved is not None and moved > 0.05:
                rep.add("solve-no-sync", FAIL,
                        f"pointing changed {moved:.2f} deg across the solve — "
                        "capture-solve appears to SYNC/RESLEW. Disable sync-on-solve "
                        "for the survey (PRD point 3).")
            else:
                rep.add("solve-no-sync", OK, "pointing unchanged across the solve")
        else:
            rep.add("solve-no-sync", UNKNOWN, "could not read coords before/after to check sync")

    # 5. (opt-in) RA-unit sanity via a no-op slew to current position.
    if slew_test:
        coords = _safe_coords(client)
        if not coords:
            rep.add("ra-unit", UNKNOWN, "no current coords to slew to; skipped")
        else:
            ra, dec = coords
            try:
                client.slew_radec(ra, dec, wait=True)
                after = _safe_coords(client)
                moved = _angular_sep_deg((ra, dec), after) if after else None
                if moved is None:
                    rep.add("ra-unit", UNKNOWN, "slew issued but could not confirm position")
                elif moved < 0.5:
                    rep.add("ra-unit", OK,
                            "slew to current position did not move — RA unit looks correct")
                else:
                    rep.add("ra-unit", FAIL,
                            f"slew to current position moved {moved:.1f} deg — likely RA-unit "
                            "mismatch (hours vs degrees); flip ninaapi.RA_IN_DEGREES")
            except Exception as exc:
                rep.add("ra-unit", FAIL, f"slew failed: {exc}")

    return rep


def _safe_coords(client) -> Optional[tuple]:
    try:
        info = client.mount_info()
        if info.ra_deg is not None and info.dec_deg is not None:
            return (info.ra_deg, info.dec_deg)
    except Exception:
        pass
    return None


def _angular_sep_deg(a, b) -> Optional[float]:
    if not a or not b:
        return None
    import math
    ra1, dec1 = math.radians(a[0]), math.radians(a[1])
    ra2, dec2 = math.radians(b[0]), math.radians(b[1])
    v = (math.sin(dec1) * math.sin(dec2)
         + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2))
    v = max(-1.0, min(1.0, v))
    return math.degrees(math.acos(v))
