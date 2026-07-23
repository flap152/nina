# Methodology & rationale

Why the tool is built the way it is, and what it does and doesn't assume. This
captures the reasoning behind the design so it survives editing (in the spirit of
the PRD, which embeds its physics rationale deliberately). Read alongside the PRD.

---

## 1. Two different "differentials" (don't conflate them)

The tool is about the **differential between the main imaging path and the guide
reference** — but "differential" means two things, and mixing them causes
confusion.

**(a) The implicit differential, inside every guided run.**
What the **main camera** records as star elongation is *not* "main-scope flexure
in isolation." It is the main imaging path moving **relative to whatever the
guider holds still**. Guiding pins the *reference* star; the main-path stars smear
by however much the imaging path drifts away from that reference during the sub.
So:

- A **guidescope-referenced** run's elongation already contains the
  main↔guidescope differential flexure (+ flop + guidescope stiction + …),
  because the guider faithfully holds the guidescope's star while the main path
  sags away from it.
- An **OAG-referenced** run shares the OTA/mirror/focuser between guide and image
  paths, so the main↔guide differential is ~zero by construction — that component
  **collapses**. (An OAG also lets the guider partially chase out slow *mirror
  flop*, since the guide star shares the flopping mirror; a guidescope cannot see
  that.)

**(b) The comparison differential, the headline output.**
To *isolate* a cause you run the survey twice and subtract:

```
guidescope-run field  −  OAG-run field  =  what the guidescope path was costing you
```

That subtraction *is* the main↔guidescope differential flexure — the "what would
an OAG buy me" number (PRD §1, §2, §6.6, implemented by `ffsurvey compare`). The
same machinery gives locks-on − locks-off and placement-A − placement-B.

A single run's map is raw material (main-path elongation relative to *that run's*
reference); the **subtraction** is where attribution to a cause happens. This is
also why the comparison is robust to confounders that cancel in the difference
(see §4).

> Note: "parallactic angle" is *not* the main-vs-guide difference. It is the
> coordinate rotation used in analysis to reach the gravity frame (§6.4a). The
> main↔guide difference is mechanical differential flexure.

---

## 2. What guiding (PHD2) is and isn't assumed to be

NINA doesn't guide; it delegates to PHD2 (over PHD2's interface), and the surveyor
drives it through the ninaAPI `equipment/guider/*` endpoints. Guiding state
(off / guidescope / OAG) is a **run-level** parameter — the independent variable
*across* comparison runs, held fixed *within* one.

We do **not** assume guiding is perfect — only *functional and consistent between
the runs being differenced*. Guiding imperfections sort into buckets:

| Guiding imperfection | Where it lands | Handled by |
|---|---|---|
| Random residual (RMS, seeing-driven) | non-repeatable scatter | burst averaging + **repeatability** metric (rejected as noise; gusts flagged) |
| Guide-**reference** mechanical defect (guidescope flexing/stiction while PHD2 chases it) | **signal** — it's one of the three families | isolated by guidescope − OAG |
| Systematic guiding bias (calibration, aggressiveness) | deterministic | **common-mode cancellation** in the A − B difference — *iff* PHD2 is consistent across the two runs |
| Mount-**axis** mechanics (backlash, stiction, belt slack) | random → repeatability; path-dependent → **hysteresis** | common-mode cancels in a diff, but is *confounded* with flop at one pointing; separated by locks-on/off and approach-pairing |

The weakest link is the **consistency requirement**: if PHD2 is recalibrated or
seeing differs wildly between the two differenced runs, systematic bias won't
cleanly cancel. Two design choices defend this:

- The runner **resumes+settles guiding after every slew but never recalibrates
  mid-survey** (calibration, if any, happens once at run start) — so the guide
  reference is stable across approaches and nodes.
- **Guide RMS is recorded per node-visit** in the sidecar, so the analyst can
  confirm guiding was comparable across differenced runs and drop nodes where it
  wasn't.
- **Guided-vs-unguided is itself a supported comparison** (PRD §3): differencing a
  guided run against an unguided one measures the guider's *net* contribution
  directly, rather than assuming it.

---

## 3. Seeing vs. signal — why the measurement works

Seeing is the largest *raw* number (star FWHM ~2–4″ = 8–16 px at 0.25″/px) but it
is the fog you see *through*, not the signal:

- **Seeing is direction-random.** Over a multi-second sub it bloats stars roughly
  *round*; its residual ellipticity is small and points in a **random** direction
  frame to frame. Flexure/flop produce a **direction-coherent, repeatable** vector
  (aligned to gravity, or flipping with approach). The discriminator is *direction
  coherence + repeatability*, not raw magnitude. The **repeatability map** is
  exactly the tool for "is this node deterministic (mechanical) or scatter
  (atmospheric)?"
- **Averaging digs signal out from under the noise.** Because seeing/guiding
  scatter is zero-mean, averaging N frames in double-angle space shrinks it by
  ~√N while the deterministic vector survives. The threshold is not "flexure >
  single-frame seeing" but "flexure > the **repeatability floor after
  averaging**," which more frames lower.
- **The noise floor scales with flexure magnitude.** Hysteresis and repeatability
  are measured in absolute double-angle units, so a fixed *angular* measurement
  scatter produces a proportionally larger vector spread at high |flexure|.
  Consequence: **near-horizon (high-flexure) nodes need more frames** to resolve a
  given flop. (This is visible in the closed-loop test's zero-flop case.)
- **Sensitivity levers:** better seeing (tighter stars), more frames/longer bursts
  (√N), and horizon nodes (largest mechanical signal).

**A null result is an upper limit, not a proven zero:** "no coherent field above
the floor *on this night, with these frames*" means flexure/flop is below your
current sensitivity — still the actionable, *quantified* answer ("differential
flexure < X px here"), bounded by the repeatability. PRD §10's success criterion —
a second night reproduces the field within the repeatability score — is how you
confirm you measured something real and stable rather than noise.

---

## 4. Atmospheric dispersion — the confounder that looks like flexure

Differential atmospheric refraction stretches every star **along the vertical**,
worse **toward the horizon** — deterministic, repeatable, vertical, and
altitude-dependent, i.e. it mimics flexure's signature far more than seeing does.
At 30° altitude in broadband it can be ~1–2″ (4–8 px), comparable to the flexure
being hunted. So a **single run's absolute flexure map** can attribute sky to the
rig at low altitude.

Two things save the result:

1. **It cancels in every differential.** Same atmosphere, same camera, same
   bandpass in both runs → dispersion is identical → guidescope − OAG (and locks,
   and placement) removes it entirely. Another reason the *subtraction*, not the
   standalone map, is the trustworthy output.
2. For a cleaner **absolute** map, shoot **narrowband** or use an **ADC**, which
   suppress dispersion at the source.

---

## 5. Exposure length, sky brightness, and stacking

Flexure elongation ∝ exposure length (it's an integrated drift). So sky darkness
and workflow matter:

- **Bright-sky broadband** (e.g. Bortle 8) forces *short*, sky-limited subs →
  little intra-sub flexure → often a non-issue.
- **Narrowband** rejects the light pollution, so sky-limited sub length grows back
  to minutes → **flexure can re-appear**. Narrowband is the regime to actually
  test in bright skies.
- **Registration/stacking already removes the slow, *inter-sub* drift** (each sub
  is star-aligned before stacking). The only flexure that survives is what smears
  *within a single sub*. So "short subs + register + stack" is the free
  first-line mitigation; the true enemy is intra-sub flexure on long subs.

### Order-of-magnitude expectations (generic long-FL SCT + guidescope; **guesses**)

At 0.25″/px. These are "what would not surprise me," not predictions — the point
of the survey is to replace them with measurements.

| Mechanism | Typical | In px | Character |
|---|---|---|---|
| Seeing (FWHM) | 2–4″ | 8–16 px | round; sets size + noise floor; directional residual ~1–2 px, random |
| Differential flexure | ~0.5–3″ (sub-px if stiff) | 2–12 px | deterministic, vertical, grows toward horizon & with exposure |
| Mirror flop | ~0 → 1–3″ when it releases | ~0, spiking to a few–10+ px | intermittent, path-dependent (→ hysteresis) |
| Atmospheric dispersion | ~1–2″ at 30° alt | 4–8 px | deterministic, vertical — mimics flexure; cancels in differentials |

Shape/centroid precision over many stars is ~0.1–0.3 px after averaging, which is
why few-px deterministic signals are resolvable under ~10 px seeing disks.

---

## 6. Feed-forward correction (future work, M3+)

Because the *flexure* component is a smooth, repeatable state function of
orientation, its measured field could in principle be **fed forward into guiding**
to cancel its effect during the exposure — a software analog of what an OAG does
in hardware.

**Mechanism.** You can't push the mount to fight the image drift (the mount moves
both paths rigidly; the guider would fight you). Instead bias the **guide lock
position** over time by the negative of the predicted differential drift, so the
mount chases the moving lock point and drags the *image* path to stay fixed. PHD2
already exposes a lock-position shift (built for comet/asteroid tracking).

**Prior art.** 10Micron mounts and Paramount's ProTrack (on a TPoint model) build
mechanical/flexure models and feed them forward for unguided/long tracking — the
same principle, one layer down (mount model vs guide loop).

**Why it's hard — "if modeled correctly."** Feed-forward is *unforgiving* where
diagnosis is not:

- **A sign error doubles the error** instead of nulling it — you push the mount
  the way the image is already drifting. PRD §9's "a sign error mirrors the whole
  field" gains teeth once you act on it.
- **Only the predictable component qualifies.** Flexure (path-independent) — yes;
  flop/stiction (path-dependent, discrete) — no. Any leakage feeds a
  non-deterministic term forward as if deterministic.
- **The model expires.** SCT refocus moves the primary (changes sensor angle *and*
  flexure); temperature shifts the tube. Re-measure, don't trust a stale model.

**Discipline: closed-loop, never open-loop.** Apply the model, then re-run the
survey and confirm the residual field **collapsed toward the repeatability floor**
(shrank ⇒ right; grew/rotated ⇒ sign/calibration wrong, caught by measuring). The
tool that builds the model is the only honest way to prove the correction worked.

Given all this, if the survey shows differential flexure is real, the robust fix
is usually the **OAG** (hardware, no model, no drift). Feed-forward is the clever
option when a hardware change isn't wanted — and only once the measurement side
has reproduced itself across nights.
