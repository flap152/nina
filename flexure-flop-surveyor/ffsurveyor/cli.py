"""Command-line entry point for the analyzer.

    ffsurvey analyze  --manifest run.json --fits 'run1/*.fits' --sidecar run1.csv --out out/
    ffsurvey compare  --a runA_nodes.json --b runB_nodes.json --label-a Guidescope --label-b OAG --out out/

The ``analyze`` command ingests one run and writes the per-node results table and
the standard plot set. ``compare`` differences two previously-analyzed runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List

from .aggregate import NodeAggregate, aggregate_nodes
from .compare import compare_runs
from .config import load_manifest
from .detect import DetectionConfig
from .ingest import ingest_run, write_node_results


def _analyze(args) -> int:
    manifest = load_manifest(args.manifest)
    detection = DetectionConfig(
        detect_sigma=args.detect_sigma,
        min_snr=args.min_snr,
        saturation=args.saturation,
    )
    report = ingest_run(
        manifest,
        fits_glob=args.fits,
        sidecar_path=args.sidecar,
        external_solver=args.solver,
        detection=detection,
    )
    print(report.summary())
    for name, reason in report.skipped:
        print(f"  skipped {name}: {reason}", file=sys.stderr)
    if not report.processed:
        print("No frames processed; nothing to write.", file=sys.stderr)
        return 1

    nodes = aggregate_nodes(report.processed, burst_filter=args.burst or None)
    os.makedirs(args.out, exist_ok=True)
    results_path = os.path.join(args.out, "node_results.json")
    write_node_results(nodes, results_path)
    print(f"Wrote {results_path}")

    if not args.no_plots:
        _render_plots(nodes, args.out)
    return 0


def _render_plots(nodes: List[NodeAggregate], out_dir: str) -> None:
    from . import plots

    figs = {
        "vector_field.png": plots.vector_field_plot(nodes),
        "hysteresis_map.png": plots.hysteresis_map(nodes),
        "repeatability_map.png": plots.repeatability_map(nodes),
    }
    for name, fig in figs.items():
        p = os.path.join(out_dir, name)
        fig.savefig(p, dpi=130)
        print(f"Wrote {p}")


def _load_nodes(path: str) -> List[NodeAggregate]:
    """Reload per-node aggregates from a results JSON for comparison."""
    from .angles import elongation_to_double_angle

    with open(path, encoding="utf-8") as fh:
        records = json.load(fh)
    nodes: List[NodeAggregate] = []
    for r in records:
        vec = elongation_to_double_angle(r["flexure_mag"], r["flexure_pa_deg"])
        nodes.append(
            NodeAggregate(
                node_id=r["node_id"],
                alt_deg=r.get("alt_deg"),
                az_deg=r.get("az_deg"),
                flexure_vector=vec,
                flexure_mag=r["flexure_mag"],
                flexure_pa_deg=r["flexure_pa_deg"],
                hysteresis_mag=r.get("hysteresis_mag", float("nan")),
                hysteresis_vector=None,
                approaches=(r.get("approaches", "") or "").split("|") if r.get("approaches") else [],
                mean_repeatability=r.get("mean_repeatability", float("nan")),
                n_frames=r.get("n_frames", 0),
            )
        )
    return nodes


def _run(args) -> int:
    """Drive a live survey over the NINA Advanced API (capture half)."""
    from .capture import NinaClient, build_survey_plan, RunnerConfig, SurveyRunner

    manifest = load_manifest(args.manifest)
    plan = build_survey_plan(
        alt_bands_deg=tuple(args.alt_bands),
        azimuths_deg=tuple(args.azimuths),
        load_axis=args.load_axis,
        offset_deg=args.offset,
        min_altitude_deg=args.min_alt,
    )
    print(f"Planned {len(plan)} node(s): {plan.notes}")
    if len(plan) == 0:
        print("No feasible nodes; check grid / offset / min-altitude.", file=sys.stderr)
        return 1

    client = NinaClient(host=args.host, port=args.port, api_key=args.api_key)
    cfg = RunnerConfig(
        exposure_s=args.exposure, burst_count=args.burst, gain=args.gain,
        settle_s=args.settle, strict_pier_side=args.strict_pier_side,
        out_dir=args.out, dry_run=args.dry_run,
    )
    runner = SurveyRunner(client, manifest, plan, cfg)
    runner.run()
    paths = runner.write_outputs()
    n_frames = len(runner.sidecar_rows)
    warns = [w for v in runner.visits for w in v.warnings]
    print(f"Captured {n_frames} frame(s) across {len(runner.visits)} node-visit(s).")
    for w in warns:
        print(f"  warning: {w}", file=sys.stderr)
    print(f"Wrote {paths['manifest']} and {paths['sidecar']}")
    print("Next: run `ffsurvey analyze` on the saved FITS + sidecar.")
    return 0


def _compare(args) -> int:
    run_a = _load_nodes(args.a)
    run_b = _load_nodes(args.b)
    comp = compare_runs(run_a, run_b, label_a=args.label_a, label_b=args.label_b)
    print(comp.summary())
    os.makedirs(args.out, exist_ok=True)
    if not args.no_plots:
        from . import plots
        fig = plots.comparison_plot(comp)
        p = os.path.join(args.out, f"compare_{args.label_a}_vs_{args.label_b}.png")
        fig.savefig(p, dpi=130)
        print(f"Wrote {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ffsurvey", description="Flexure & Flop Surveyor analyzer")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="analyze one survey run")
    a.add_argument("--manifest", required=True)
    a.add_argument("--fits", required=True, help="glob for the run's FITS files")
    a.add_argument("--sidecar", required=True, help="node/approach/burst CSV")
    a.add_argument("--out", required=True, help="output directory")
    a.add_argument("--solver", choices=["astap"], default=None,
                   help="external solver for frames without an embedded WCS")
    a.add_argument("--burst", default=None, help="only use this burst tag (e.g. post_settle)")
    a.add_argument("--detect-sigma", type=float, default=5.0)
    a.add_argument("--min-snr", type=float, default=10.0)
    a.add_argument("--saturation", type=float, default=None)
    a.add_argument("--no-plots", action="store_true")
    a.set_defaults(func=_analyze)

    r = sub.add_parser("run", help="drive a live survey over the NINA Advanced API")
    r.add_argument("--manifest", required=True)
    r.add_argument("--out", required=True, help="output dir for FITS/manifest/sidecar")
    r.add_argument("--host", default="localhost")
    r.add_argument("--port", type=int, default=1888)
    r.add_argument("--api-key", default=None)
    r.add_argument("--alt-bands", type=float, nargs="+", default=[30.0, 50.0, 70.0, 80.0])
    r.add_argument("--azimuths", type=float, nargs="+", default=[0.0, 90.0, 180.0, 270.0])
    r.add_argument("--load-axis", choices=["altitude", "dec"], default="altitude")
    r.add_argument("--offset", type=float, default=8.0, help="offset-then-slew-in leg (deg)")
    r.add_argument("--min-alt", type=float, default=20.0)
    r.add_argument("--exposure", type=float, default=5.0)
    r.add_argument("--burst", type=int, default=5)
    r.add_argument("--gain", type=int, default=None)
    r.add_argument("--settle", type=float, default=5.0)
    r.add_argument("--strict-pier-side", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="plan without commanding gear")
    r.set_defaults(func=_run)

    c = sub.add_parser("compare", help="difference two analyzed runs")
    c.add_argument("--a", required=True, help="run A node_results.json")
    c.add_argument("--b", required=True, help="run B node_results.json")
    c.add_argument("--label-a", default="A")
    c.add_argument("--label-b", default="B")
    c.add_argument("--out", required=True)
    c.add_argument("--no-plots", action="store_true")
    c.set_defaults(func=_compare)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
