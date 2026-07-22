# Capture half — building the survey in NINA

This is the data-generation side of the Flexure & Flop Surveyor (PRD §5.1). It
runs entirely in **stock NINA's Advanced Sequencer** — no plugin is required for
v1. You build the survey once as a sequence template, then run it per night /
per configuration.

The analysis half (the `ffsurveyor` Python package) never talks to NINA; the two
halves are connected only by FITS files on disk plus a small **sidecar CSV**
(PRD §5).

---

## The one thing that isn't automatic: the sensor rotation angle

The whole tool depends on knowing, per frame, the camera's rotation on the sky
(PRD §6.4a). **Stock NINA does not write the plate-solved sensor angle into FITS
headers.** It writes only:

- `OBJCTROT` — the *planned* target position angle (not the solved sensor angle),
- `ROTATANG` — a *mechanical rotator* angle, and only if you own a rotator.

The plate solver *computes* the sensor `PositionAngle`, but it is used to drive a
rotator and then discarded. So we do **not** rely on NINA to record it.

**Resolution (chosen approach): the analyzer re-solves every frame itself.** As
long as each saved science frame can be plate-solved (either it already carries a
WCS, or the analyzer runs ASTAP / astrometry.net on it), `ffsurveyor` recovers
the sensor→sky rotation directly from the WCS. This makes §6.4a independent of
NINA and is also more robust across capture setups (PRD §9, "leaning re-derive").

Practical consequence for the sequence: **your frames must be solvable.** Use a
normal star field (don't point at a blank field), keep subs long enough for good
star SNR, and — if you don't want to depend on a WCS being embedded — just make
sure the analyzer has ASTAP or a local astrometry.net index installed.

> If you later prefer NINA to stamp the angle at capture time, add an
> **External Script** item after the solve that appends the solved PA to the
> sidecar CSV, or build the optional M3 plugin that injects it via
> `ImageMetaData.GenericHeaders`. Neither is needed for v1.

---

## Sequence building blocks (all stock)

These are the concrete Advanced Sequencer items the survey uses:

| Purpose | Sequence item |
|---|---|
| Slew to a grid node | **Slew to Alt/Az** (`SlewScopeToAltAz`) or **Slew to RA/Dec** (`SlewScopeToRaDec`) |
| Deliberate offset for the approach leg | **Slew to Alt/Az** to an offset point, then slew in (see approach pairing) |
| Plate-solve + recenter | **Center** (`Center`) — solves and recenters on the node |
| Settle | **Wait for Time Span** (`WaitForTimeSpan`) |
| Capture burst | **Take Many Exposures** (`TakeManyExposures`) or a loop of **Take Exposure** |
| Per-node bookkeeping | **Define Scoped Variable** (`Variable`), **External Script** (`ExternalScript`) |
| Flow control | **Sequential**/**Conditional** containers, **Loop While** conditions |

Nothing here is custom code.

---

## Structure of the survey

Per **grid node**, per **approach direction** (PRD §5.1):

```
For each node in grid:
  For each approach in {high, low}:           # the two approach legs (§6.2)
    1. Slew to an OFFSET point on the approach side
       (e.g. +8° in altitude for "high", −8° for "low")   ← defeats anti-backlash
    2. Slew IN to the node (this final leg is the experimental variable, §6.2b)
    3. Center (plate-solve + recenter)         ← frames become solvable/pointing known
    4. Take Exposure ×K   → burst "immediate"  ← catches a flop that releases post-slew
    5. Wait for Time Span (settle)             ← configurable; don't over-settle (§6.3)
    6. Take Exposure ×K   → burst "post_settle"
    7. External Script: append rows to the sidecar CSV
```

### Approach pairing (the crux — PRD §6.2 / §6.2b)

The two approaches must differ in the **direction of the last load on the
mirror**, not in tracking rate. Two practical axes:

- **Altitude sense** — approach from higher altitude vs. lower altitude (loads the
  mirror in opposite senses along the tube axis).
- **Dec sense** — approach after a large +Dec vs. −Dec slew.

Constraints the plan must respect (PRD §6.2b):

1. **Same pier side for both approaches.** A meridian flip between the two
   approaches changes the whole geometry (including sensor angle) far more than
   the mirror-load direction. Force both onto the same pier side. If unavoidable,
   record the flip — the analyzer's per-solve angle normalization can still bring
   both into a common frame, but same-pier-side is strongly preferred.
2. **Explicitly command the final approach leg.** Many mounts finish every slew
   from the same side (anti-backlash), so a single GoTo will not actually differ
   in final-load direction and the hysteresis measurement collapses to zero for
   the wrong reason. Always do **offset-then-slew-in**, and disable any
   "always approach from one side" setting for the survey's duration.
3. **Achievable pair is node-dependent.** Near the horizon you can't approach
   "from below"; near the meridian a clean ±Dec pair may need a flip. Pick, per
   node, a pair that is reachable, same-pier-side, and genuinely opposite in
   mirror-load sense.
4. **Reference rig (CGX / EdgeHD):** which load axis is cleanly exercisable is an
   M0 empirical unknown — determine it in the M0 proof of concept (PRD §8, §9).

---

## Tagging frames: the sidecar CSV

NINA won't put `node_id` / `approach` / `burst` into headers, so we key them by
filename in a sidecar the sequence appends to (PRD §6.4, §9). Format:

```csv
filename,node_id,approach,burst
2026-01-15_210012_L_0002.fits,Alt30-Az090,high,post_settle
```

Two easy ways to produce it:

- **External Script item** after each burst that echoes a line per just-saved
  file. Use scoped Variables for `node_id`/`approach`/`burst` and NINA's
  image-file-path token for the filename.
- Or post-process: if you encode the node/approach/burst into NINA's **file-name
  pattern** (e.g. `$$TARGETNAME$$` set per node), a tiny script can regenerate the
  sidecar from filenames afterward.

The run-level state (guiding mode, guide-config identity + placement, mirror-lock
state, site, grid id) goes in the **run manifest** JSON — one per run — not in the
sidecar. See `examples/manifest.example.json`.

---

## Run-level parameters (one value per run — never varied mid-run)

Set these before the run and record them in the manifest (PRD §5.1, §6.5b):

- **Guiding mode**: off / guidescope / OAG.
- **Guide-configuration identity + placement**: e.g. `GS-60mm-toprings` vs
  `GS-60mm-saddle` vs `OAG`, with model/FL, mounting method, and mount point.
  These are themselves flexure variables and are what the comparison runs diff.
- **Mirror-lock state**: on / off.

A comparison (OAG benefit, locks, placement) is simply two runs that differ in
exactly one of these, analyzed and differenced (`ffsurvey compare`).

---

## Grid defaults (PRD §6.1)

- Altitude bands **30°, 50°, 70°, near-zenith** × several azimuths.
- Add explicit **near-meridian** and **near-horizon** nodes (flexure-rate
  extremes).
- **Avoid the immediate zenith** (mount keyhole / fast azimuth slew); skip nodes
  below a min-altitude cutoff.
- Keep node count modest for v1 so a survey fits in a usable fraction of a night;
  make density a config knob.

---

## Checklist before you trust a run

- [ ] Every frame plate-solves (embedded WCS, or ASTAP/astrometry.net available
      to the analyzer).
- [ ] Both approaches to each node are on the **same pier side**.
- [ ] The final approach leg is an explicit **offset-then-slew-in**, and any
      "approach from one side" mount setting is disabled.
- [ ] Sidecar CSV has one row per saved frame with correct `node_id`/`approach`/
      `burst`.
- [ ] Manifest records guiding mode, guide-config identity/placement, and
      mirror-lock state.
- [ ] Confirm the plate solver's rotation-angle sign convention once (PRD §9);
      with the WCS path the analyzer derives it numerically, but validate on M0.
