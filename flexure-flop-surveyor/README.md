# Flexure & Flop Surveyor

Map mechanical star-elongation error across the sky and **separate its physical
causes** — differential flexure, mirror flop, and guidescope stiction — on a
long-focal-length rig. A NINA survey generates the data; a portable Python
analyzer turns it into gravity-frame vector-field maps and quantified comparison
numbers (e.g. *what an OAG actually buys you*).

This repository is the reference implementation of the
[Flexure & Flop Surveyor PRD](docs/) — a self-contained project, deliberately
**not** part of any NINA source tree (see *Why not a NINA fork?* below).

---

## The idea in one paragraph

At long focal length, stars go non-round for several superimposed reasons.
Three of them are mechanical and are normally confounded at any one pointing:
**flexure** (path-independent, gravity-driven), **flop** (path-dependent
hysteresis in the imaging path), and **guidescope stiction** (path-dependence in
the *reference*). They separate by their physical signatures if you (a) visit a
sky grid, (b) hit each node from **two approach directions**, and (c) measure the
star-elongation **vector** rotated into a common **gravity frame**. Flexure is
the part common to both approaches; hysteresis is their difference; seeing/wind
is the non-repeatable scatter within a burst. Differencing two whole runs
(guidescope vs OAG, locks on/off, placement A/B) answers the hardware questions.

Full rationale: **[the PRD](docs/)** and the physics table in PRD §2.

---

## Two halves, connected only by FITS

```
[ NINA survey sequence ]  →  FITS (+ headers) + sidecar CSV  →  [ ffsurveyor ]  →  maps + scores
        capture                        data                        analysis          output
```

- **Capture** drives NINA **live over the Advanced API** (`ninaAPI`, port 1888) —
  raw slews, controlled offset-then-slew-in approaches, read-only solves. It does
  *not* generate a sequence file to import. See
  **[docs/nina-live-orchestration.md](docs/nina-live-orchestration.md)**
  (a manual-sequence fallback for M0 is in
  [docs/nina-sequence.md](docs/nina-sequence.md)).
- **Analysis** is this Python package (`ffsurveyor`), portable and hardware-free.

### The load-bearing prerequisite: sensor-angle normalization

All elongation is measured in **sensor pixel coordinates**, but the physical
error lives in a **sky/gravity frame**. The transform is the camera's rotation on
the sky, which must be resolved **per frame** (an SCT's focus moves the primary,
so the angle isn't even stable across a night — PRD §6.4a). **Stock NINA does not
record the plate-solved sensor angle**, so this analyzer **re-solves every frame
itself** and derives the rotation numerically from the WCS Jacobian — which also
sidesteps the plate-solver sign-convention trap (PRD §9). Without this step the
flexure field looks like noise and no between-run comparison is possible.

---

## Install

```bash
cd flexure-flop-surveyor
pip install -e .            # numpy, astropy, photutils, matplotlib, scipy
# optional, for solving frames that lack an embedded WCS:
#   install ASTAP (+ a star database) or a local astrometry.net index
```

Python 3.9+.

## Use

Preflight-check a live NINA (orchestrator and NINA may be on different LAN machines):

```bash
ffsurvey doctor --host 192.168.1.50 --port 1888   # add --capture-test / --slew-test on a rig
```

Drive a live survey (capture half — needs NINA + the Advanced API plugin running):

```bash
ffsurvey run \
    --manifest examples/manifest.example.json \
    --out survey_run/ \
    --host localhost --port 1888 \
    --load-axis altitude --offset 8
# add --dry-run to plan and write the sidecar without commanding any gear
```

Analyze one run:

```bash
ffsurvey analyze \
    --manifest examples/manifest.example.json \
    --fits 'my_run/*.fits' \
    --sidecar my_run_sidecar.csv \
    --out out/ \
    --solver astap            # omit if frames already carry a WCS
```

Produces `out/node_results.json` plus `vector_field.png`, `hysteresis_map.png`,
`repeatability_map.png` (PRD §6.7).

Compare two analyzed runs (the headline OAG-benefit / locks / placement diff):

```bash
ffsurvey compare \
    --a guidescope/node_results.json --label-a Guidescope \
    --b oag/node_results.json        --label-b OAG \
    --out out/
```

### As a library

```python
from ffsurveyor import (load_manifest, ingest_run, aggregate_nodes, compare_runs)

manifest = load_manifest("run.json")
report   = ingest_run(manifest, "my_run/*.fits", "my_run_sidecar.csv", external_solver="astap")
nodes    = aggregate_nodes(report.processed, burst_filter="post_settle")
```

---

## What each module does

| Module | Responsibility | PRD |
|---|---|---|
| `angles.py` | Double-angle elongation vectors; sensor→sky→gravity rotations; parallactic angle | §6.4a, §6.5 |
| `detect.py` | Star detection + second moments → robust per-frame sensor-frame elongation | §6.5 (1–3) |
| `solve.py` | Per-frame plate solve (header WCS / ASTAP); WCS-Jacobian sensor→sky rotation | §6.4a |
| `frame.py` | Orchestrate one frame → gravity-frame vector (adds parallactic rotation) | §6.5 (4) |
| `aggregate.py` | Burst/node/approach aggregation; repeatability, flexure, hysteresis scores | §6.6 |
| `compare.py` | Run-pair diffs (OAG, locks, placement) in the common gravity frame | §6.6 |
| `plots.py` | Alt/az vector field, hysteresis map, repeatability map, comparison | §6.7 |
| `ingest.py` | FITS + sidecar ingestion; results export | §6.4, §6.7 |
| `config.py` | Run manifest + grid definitions (guide-config identity & placement) | §6.1, §6.5b |
| `capture/ninaapi.py` | Live NINA Advanced API client (raw slew, read-only capture-solve) | points 1–3 |
| `capture/planner.py` | Grid + approach-pair planning (offset-then-slew-in, feasibility) | §6.1, §6.2b |
| `capture/runner.py` | Survey loop enforcing the invariants; writes manifest + sidecar | §5.1, points 2–6 |
| `capture/doctor.py` | Live preflight: reachability, pier-side key, no-sync solve, RA-unit | §8, §9 |

Both frames are kept per node (PRD point 9): `aggregate.py` computes flexure and
hysteresis in the **gravity** frame *and* the **sensor** frame, so a sensor-fixed
error (tilt, pinched optic) that is coherent only in the sensor frame is
distinguishable from a gravity-driven one.

The two mathematically sensitive stages — the angle transforms and the detection
— are validated against independent references and synthetic ground truth in
`tests/` (parallactic angle vs a 3D vector-geometry computation; PA recovery from
synthetic elliptical star fields; a full synthetic-FITS pipeline run).

```bash
pip install pytest && pytest -q
```

---

## Milestones (from the PRD)

- **M0** — manual proof of concept: ~6 nodes, 2 approaches, one quiver plot.
  See **[docs/M0-checklist.md](docs/M0-checklist.md)**.
- **M1** — config-driven survey: grid/settle/burst/approach parameterized;
  feasibility planner; full output set. *(analyzer side largely in place)*
- **M2** — comparison runs: guidescope/OAG, locks, placement, with run tracking.
- **M3 (optional)** — a NINA plugin wrapping sequence generation + one-click run.

---

## Why not a NINA fork?

The deliverable is a Python tool that drives NINA over its **Advanced API** plus
this analyzer — neither is a change to NINA's source. The capture half is an API
client (`ffsurveyor/capture/`) talking to the running NINA app; the analysis half
never touches NINA at all. A NINA fork is only useful as an *API reference* if you
later build the optional **M3 plugin**, and even then a NINA plugin is a separate
MEF-composed DLL built against NINA assemblies — it doesn't live inside NINA's
solution either. So this project stands alone; keep a NINA checkout nearby only
for M3.

---

## Status

v0.1 — analysis pipeline complete and tested end-to-end on synthetic data;
capture-half design documented. Not yet exercised on real sky frames (needs a
rig). The plate-solver rotation-angle sign convention should be confirmed on M0
(PRD §9), though the WCS path derives it numerically.
