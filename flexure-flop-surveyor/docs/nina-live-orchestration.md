# Capture half — live orchestration over the NINA Advanced API

This is the **primary** capture architecture (PRD points 1, 12): the tool drives
NINA *live* over the `ninaAPI` Advanced API (REST, default port **1888**). It does
**not** generate a static NINA sequence for the user to import. (A hand-built
sequence is only a manual fallback for the M0 proof of concept — see
[`nina-sequence.md`](nina-sequence.md).)

End-state flow (point 12): the user connects gear in NINA, enables the Advanced
API plugin, and runs one command — no hand-built sequence, no mid-survey clicking:

```bash
ffsurvey run --manifest run.json --out survey_run/ --host localhost --port 1888
# then, after the run:
ffsurvey analyze --manifest survey_run/manifest.json \
    --fits 'survey_run/*.fits' --sidecar survey_run/sidecar.csv --out out/ --solver astap
```

Implementation: `ffsurveyor/capture/` — `ninaapi.py` (client), `planner.py`
(grid + approach pairs), `runner.py` (the survey loop that enforces the
invariants).

---

## Design invariants enforced in code

| # | Invariant | Where enforced |
|---|---|---|
| 2 | **Raw slews only** at nodes; never Slew-and-Center | `NinaClient` exposes only `slew_radec` (raw `equipment/mount/slew`); there is no center method to call |
| 3 | **Plate solve is read-only** — no sync/reslew at nodes | angle is read from the capture-solve response (`PlateSolveResult.Rotation`); no sync/solve-and-reslew endpoint is ever called |
| 4 | **Controlled offset-then-slew-in** final leg | `runner._run_leg` issues two raw slews: to the offset point, then to the node |
| 5 | **Same pier side** for both approaches | `runner` reads `equipment/mount/info` after each arrival and flags/aborts on mismatch (`strict_pier_side`) |
| 6 | **Immediate + post-settle bursts** | `runner._run_leg` captures the immediate burst *before* the settle sleep, then the post-settle burst after |

Tracking rate is held constant (sidereal) for the whole survey — only arrival
*history* is varied, never rate (PRD 6.2).

---

## Endpoint provenance (ninaAPI v2)

Base URL `http://<host>:<port>/v2/api/<endpoint>`, default port **1888**. Routes
below are the ones observed in the ninaAPI v2 surface (repo
`christian-photo/ninaAPI`; cross-checked against the community MCP wrapper
`PaDev1/Nina_advanced_api_mcp`).

| Operation | Route | Notes |
|---|---|---|
| Raw slew to RA/Dec | `GET equipment/mount/slew?ra=&dec=&waitForResult=` | raw GoTo, no centering |
| Stop slew | `GET equipment/mount/stop-slew` | |
| Tracking mode | `GET equipment/mount/tracking?mode=` | held at Sidereal |
| Mount info (pier side, coords) | `GET equipment/mount/info` | pier-side field name defensively parsed |
| Capture (+ optional solve) | `GET equipment/camera/capture?exposure=&gain=&binning=&filter=&save=&filename=&solve=&solve_timeout=&waitForResult=` | `solve=true` returns `PlateSolveResult{Rotation,Ra,Dec,PixelScale}` and does **not** move the mount |

### Items to confirm against a live instance (do this in M0)

These are called out explicitly because a wrong guess is silently damaging:

1. **RA unit on `equipment/mount/slew`.** The reference client sends `ra` in
   **degrees** (it multiplies input hours by 15). `ninaapi.RA_IN_DEGREES` encodes
   this; if the endpoint actually wants hours, flip it. A wrong choice mis-points
   every slew by 15×. *(Verify first — it gates everything.)*
2. **`equipment/mount/slew` is a pure GoTo** with no built-in centering or
   settling reversal. If your driver adds an anti-backlash "approach from one
   side" on the final leg, disable it for the survey (see below) or the offset-
   then-slew-in produces identical loads and hysteresis reads ~0 for the wrong
   reason (PRD 6.2b).
3. **Capture-solve does not sync/reslew.** Confirm the camera capture `solve`
   path is a solve-only readout (no "solve and sync", no recenter). If your NINA
   is configured to sync on solve, turn that off for the survey.
4. **`PlateSolveResult.Rotation` sign/reference convention** (PRD 6.4a, 9). The
   analyzer re-derives the angle numerically from each frame's WCS, so this is a
   cross-check, not the authority — but confirm the sign once so the cross-check
   is meaningful.
5. **Pier-side field name** in `equipment/mount/info` (`SideOfPier` vs
   `PierSide`). The client parses several spellings; verify yours is covered.
6. **Alt/Az slewing.** No raw Alt/Az slew route was confirmed in the wrapper, so
   nodes (defined in alt/az) are converted to RA/Dec at command time and slewed
   via `equipment/mount/slew`. If a native alt/az slew exists and you prefer it,
   add it to the client.
7. **Saved FITS filename.** The runner passes `filename=` and keys the sidecar to
   it. Confirm NINA saves to exactly that name/path (adjust the image file-path
   pattern if it prefixes a directory or token).

---

## Disabling anti-backlash for the survey (point 4)

The offset-then-slew-in only creates *opposite* final loads if the mount driver
isn't silently reversing the final leg. Before a run:

- In the mount/ASCOM driver, disable any "approach from one side" / anti-backlash
  overshoot-and-return for the survey's duration.
- Keep both approaches on the **same pier side** (the runner enforces this) so a
  flip doesn't dwarf the mirror-load difference.
- On the reference CGX, respect its meridian-flip and slew conventions; which
  load axis (altitude vs Dec) is cleanly exercisable is an M0 unknown — the
  `--load-axis` flag lets you try both.

---

## What the runner writes

Into `--out`:

- `manifest.json` — run-level state (guiding mode, guide-config identity +
  placement, mirror-lock state, site) for cross-run comparison (PRD 6.5b, 11).
- `sidecar.csv` — one row per frame: `filename, node_id, approach, burst,
  timestamp, commanded_ra/dec/alt/az, pier_side, nina_rotation_deg`. The
  `nina_rotation_deg` is NINA's own solve angle, recorded as a **cross-check**;
  the analyzer re-solves each frame from pixels for the authoritative angle
  (PRD 6.4a, 9).

Then hand the FITS + sidecar to `ffsurvey analyze`.
