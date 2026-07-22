"""Live capture-half orchestration over the NINA Advanced API (`ninaAPI`).

This subpackage drives NINA *live* (REST, default port 1888) rather than
generating a static sequence file for the user to import. It enforces the survey
design invariants in code:

* raw mount slews at nodes (never NINA's Slew-and-Center);
* plate solve for read-only pointing/rotation (no mount sync/reslew);
* controlled offset-then-slew-in final approach leg;
* same pier side for both approaches to a node;
* immediate + post-settle bursts per node-visit.

Endpoints target `christian-photo/ninaAPI` v2 (`/v2/api/...`). See
`docs/nina-live-orchestration.md` for the endpoint provenance and the items that
still need confirmation against a live NINA instance.
"""

from .ninaapi import NinaClient, NinaApiError, PlateSolveReadout
from .planner import ApproachLeg, NodePlan, SurveyPlan, build_survey_plan
from .runner import RunnerConfig, SurveyRunner
from .doctor import Check, DoctorReport, run_doctor

__all__ = [
    "NinaClient",
    "NinaApiError",
    "PlateSolveReadout",
    "ApproachLeg",
    "NodePlan",
    "SurveyPlan",
    "build_survey_plan",
    "RunnerConfig",
    "SurveyRunner",
    "Check",
    "DoctorReport",
    "run_doctor",
]
