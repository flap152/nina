# M0 — Manual proof of concept

Goal (PRD §8): a hand-built NINA sequence visiting ~6 nodes, 2 approaches each,
bursts logged, **plate-solve angle captured per node** (here: recoverable by
re-solving in Python). Python extracts vectors, rotates to the gravity frame, and
draws one alt/az quiver plot.

M0 exists to validate three things cheaply, before investing in the full
config-driven survey:

1. **The vector field is legible after rotation** — flexure shows up as a smooth,
   gravity-aligned pattern only once vectors are rotated into the gravity frame.
2. **Approach pairing actually reveals hysteresis on this rig** — the two
   approaches differ where flop is present.
3. **The chosen load axis genuinely changes the hysteresis number** — if
   altitude-sense (or Dec-sense) approaches produce no difference, the approach
   definition or load axis is wrong (PRD §9). Cheap to learn now.

---

## Capture (NINA)

1. Pick **6 nodes** spanning altitude (e.g. 30°, 50°, 70°) at a couple of
   azimuths, all on **one pier side**.
2. For each node, script **two approaches** (`high` / `low`) as
   offset-then-slew-in (see `nina-sequence.md`). Disable any "approach from one
   side" mount setting.
3. At each node-visit: **Center** (solve+recenter), **burst of K≈5** short subs
   tagged `immediate`, **settle**, another burst tagged `post_settle`.
4. Append a **sidecar CSV** row per frame (`filename,node_id,approach,burst`).
5. Fill in the **run manifest** (`examples/manifest.example.json`) with your site,
   guiding mode, guide-config identity, and mirror-lock state.

Keep subs short enough that a single sub isn't smeared by tracking, long enough
for good centroid SNR.

## Analyze (Python)

```bash
pip install -e .            # from the project root
ffsurvey analyze \
    --manifest run.json \
    --fits 'run_m0/*.fits' \
    --sidecar run_m0_sidecar.csv \
    --out out_m0/ \
    --solver astap          # omit if frames already carry a WCS
```

Outputs in `out_m0/`:

- `vector_field.png` — the alt/az flexure quiver (gravity frame).
- `hysteresis_map.png` — path-dependent (flop) magnitude per node.
- `repeatability_map.png` — separates mechanical from atmospheric nodes.
- `node_results.json` — machine-readable per-node table.

## Pass / fail reading

- **Legibility:** in `vector_field.png`, do the flexure ticks vary smoothly with
  alt/az (largest near the horizon, aligned to the local vertical)? If they look
  random, suspect a rotation-sign error (PRD §9) — re-check the solver angle
  convention before blaming the mount.
- **Hysteresis present?** In `hysteresis_map.png`, are some nodes clearly hotter
  than the repeatability floor? That is real flop / guidescope stiction.
- **Load axis works?** Re-run one node with the *other* load axis (Dec-sense vs
  altitude-sense) and compare `hysteresis_mag`. If neither axis moves the number,
  the approach isn't loading the mirror — fix that before M1.

## What to record for M1

- Which load axis (altitude vs Dec) produced a usable hysteresis signal.
- Which approach pairs were mechanically achievable per node (feeds the §6.2b
  planner).
- Rough time per node → informs how many nodes fit in a night.
- Confirmed solver rotation-angle sign convention.
