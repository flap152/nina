"""Rendering of the survey outputs (PRD 6.7).

All plots are in the gravity frame. Elongation is a 180-deg-ambiguous
orientation, so it is drawn as a symmetric tick (a line segment), never an
arrow. In the alt/az panels a tick pointing straight up (toward higher
altitude) is aligned with the local vertical, i.e. gravity-frame PA = 0.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np

import matplotlib
matplotlib.use("Agg")  # headless-safe
import matplotlib.pyplot as plt  # noqa: E402

from .aggregate import NodeAggregate  # noqa: E402
from .compare import RunComparison  # noqa: E402

__all__ = [
    "vector_field_plot",
    "hysteresis_map",
    "repeatability_map",
    "comparison_plot",
]


def _tick_endpoints(az, alt, pa_deg, length):
    """Symmetric tick centered at (az, alt), oriented at gravity PA (0 = up)."""
    t = np.radians(pa_deg)
    # Rotate the "up" unit vector (0, 1) CCW by t: (-sin t, cos t).
    dx, dy = -np.sin(t), np.cos(t)
    hx, hy = 0.5 * length * dx, 0.5 * length * dy
    return [az - hx, az + hx], [alt - hy, alt + hy]


def vector_field_plot(
    nodes: Sequence[NodeAggregate],
    title: str = "Flexure field (gravity frame)",
    tick_scale_deg: float = 12.0,
):
    """Alt/az oriented-tick map of the per-node flexure vector."""
    fig, ax = plt.subplots(figsize=(9, 6))
    mags = [n.flexure_mag for n in nodes if not np.isnan(n.flexure_mag)]
    mmax = max(mags) if mags else 1.0
    mmax = mmax if mmax > 0 else 1.0

    for n in nodes:
        if n.az_deg is None or n.alt_deg is None or np.isnan(n.flexure_mag):
            continue
        length = tick_scale_deg * (n.flexure_mag / mmax)
        xs, ys = _tick_endpoints(n.az_deg, n.alt_deg, n.flexure_pa_deg, length)
        ax.plot(xs, ys, "-", color="C0", lw=2, solid_capstyle="round")
        ax.plot(n.az_deg, n.alt_deg, ".", color="0.3", ms=4)
        ax.annotate(n.node_id, (n.az_deg, n.alt_deg), fontsize=7,
                    xytext=(3, 3), textcoords="offset points", color="0.4")

    ax.set_xlabel("Azimuth (deg)")
    ax.set_ylabel("Altitude (deg)")
    ax.set_xlim(0, 360)
    ax.set_ylim(0, 90)
    ax.set_title(f"{title}\n(tick length ∝ magnitude; up = local vertical)")
    ax.grid(True, alpha=0.3)
    _add_scale_note(ax, mmax)
    fig.tight_layout()
    return fig


def _add_scale_note(ax, mmax):
    ax.text(0.99, 0.01, f"max |elong| = {mmax:.3f}", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=8, color="0.4")


def hysteresis_map(nodes: Sequence[NodeAggregate], title: str = "Hysteresis (flop) map"):
    """Per-node path-dependent (flop) magnitude as a colored scatter."""
    fig, ax = plt.subplots(figsize=(9, 6))
    az = [n.az_deg for n in nodes if n.az_deg is not None and not np.isnan(n.hysteresis_mag)]
    alt = [n.alt_deg for n in nodes if n.az_deg is not None and not np.isnan(n.hysteresis_mag)]
    h = [n.hysteresis_mag for n in nodes if n.az_deg is not None and not np.isnan(n.hysteresis_mag)]
    if az:
        sc = ax.scatter(az, alt, c=h, s=160, cmap="magma", edgecolor="k", linewidth=0.5)
        fig.colorbar(sc, ax=ax, label="hysteresis magnitude (approach diff)")
    ax.set_xlabel("Azimuth (deg)")
    ax.set_ylabel("Altitude (deg)")
    ax.set_xlim(0, 360)
    ax.set_ylim(0, 90)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def repeatability_map(nodes: Sequence[NodeAggregate], title: str = "Repeatability map"):
    """Per-node within-burst scatter: low = mechanical, high = atmospheric."""
    fig, ax = plt.subplots(figsize=(9, 6))
    az = [n.az_deg for n in nodes if n.az_deg is not None and not np.isnan(n.mean_repeatability)]
    alt = [n.alt_deg for n in nodes if n.az_deg is not None and not np.isnan(n.mean_repeatability)]
    r = [n.mean_repeatability for n in nodes if n.az_deg is not None and not np.isnan(n.mean_repeatability)]
    if az:
        sc = ax.scatter(az, alt, c=r, s=160, cmap="viridis", edgecolor="k", linewidth=0.5)
        fig.colorbar(sc, ax=ax, label="within-burst scatter (low = deterministic)")
    ax.set_xlabel("Azimuth (deg)")
    ax.set_ylabel("Altitude (deg)")
    ax.set_xlim(0, 360)
    ax.set_ylim(0, 90)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def comparison_plot(comp: RunComparison, tick_scale_deg: float = 12.0):
    """Overlay flexure ticks for run A and run B, plus the A-B diff tick."""
    fig, ax = plt.subplots(figsize=(9, 6))
    mags = [max(n.flexure_a, n.flexure_b) for n in comp.nodes]
    mmax = max(mags) if mags else 1.0
    mmax = mmax if mmax > 0 else 1.0

    for n in comp.nodes:
        if n.az_deg is None or n.alt_deg is None:
            continue
        # A and B ticks (share the flexure diff vector's PA only for the diff).
        la = tick_scale_deg * (n.flexure_a / mmax)
        lb = tick_scale_deg * (n.flexure_b / mmax)
        # PA of each isn't stored separately in the comparison; show magnitudes
        # as vertical bars offset left/right, and the diff vector as a tick.
        ax.plot([n.az_deg - 2, n.az_deg - 2], [n.alt_deg, n.alt_deg + la],
                "-", color="C3", lw=3, alpha=0.7)
        ax.plot([n.az_deg + 2, n.az_deg + 2], [n.alt_deg, n.alt_deg + lb],
                "-", color="C2", lw=3, alpha=0.7)
        ax.plot(n.az_deg, n.alt_deg, "k.", ms=4)

    ax.plot([], [], "-", color="C3", lw=3, label=f"{comp.label_a} |flexure|")
    ax.plot([], [], "-", color="C2", lw=3, label=f"{comp.label_b} |flexure|")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_xlabel("Azimuth (deg)")
    ax.set_ylabel("Altitude (deg)")
    ax.set_xlim(0, 360)
    ax.set_ylim(0, 90)
    ax.set_title(
        f"{comp.label_a} vs {comp.label_b}  "
        f"(mean flexure {comp.mean_flexure_a:.3f} → {comp.mean_flexure_b:.3f})"
    )
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig
