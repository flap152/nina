"""Flexure & Flop Surveyor — analysis half.

Maps mechanical star-elongation error across the sky and separates its physical
causes (flexure vs. flop vs. guidescope stiction) from FITS captured by a NINA
survey sequence. See the project README and the PRD for the full rationale.
"""

from .angles import (
    DoubleAngleVector,
    SensorAngleConvention,
    elongation_to_double_angle,
    double_angle_to_pa_mag,
    parallactic_angle_deg,
    sky_to_gravity_double_angle,
)
from .config import GridNode, GuideConfig, RunManifest, Site, load_manifest
from .detect import DetectionConfig, FrameElongation, measure_frame
from .solve import FrameSolution, solve_frame, solve_from_header
from .frame import FrameResult, process_frame
from .aggregate import BurstAggregate, NodeAggregate, aggregate_bursts, aggregate_nodes
from .compare import NodeComparison, RunComparison, compare_runs
from .ingest import IngestReport, ingest_run, read_sidecar, write_node_results

__version__ = "0.1.0"

__all__ = [
    "DoubleAngleVector",
    "SensorAngleConvention",
    "elongation_to_double_angle",
    "double_angle_to_pa_mag",
    "parallactic_angle_deg",
    "sky_to_gravity_double_angle",
    "GridNode",
    "GuideConfig",
    "RunManifest",
    "Site",
    "load_manifest",
    "DetectionConfig",
    "FrameElongation",
    "measure_frame",
    "FrameSolution",
    "solve_frame",
    "solve_from_header",
    "FrameResult",
    "process_frame",
    "BurstAggregate",
    "NodeAggregate",
    "aggregate_bursts",
    "aggregate_nodes",
    "NodeComparison",
    "RunComparison",
    "compare_runs",
    "IngestReport",
    "ingest_run",
    "read_sidecar",
    "write_node_results",
    "__version__",
]
