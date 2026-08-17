"""A small harness framework: coordination mechanisms as self-describing affordances.

``team_harness`` models a feature as a boolean that gates runtime plumbing,
with the prompt text describing that feature living somewhere else entirely.
The two drift.  Measured on ``main``: 4 of 10 (feature, role) combinations
leak prompt text for a mechanism that has been switched off, and the two worst
offenders — ``scratchpad`` and ``task_list`` — are exactly the two ablation
arms that showed large drops.

This module ties them together.  An :class:`~cooperbench.harness.affordance.Affordance`
owns its capability, its description, and the list of artifacts it introduces,
so:

* ablating a mechanism removes its prompt text, because they are one object;
* :meth:`~cooperbench.harness.affordance.HarnessSpec.audit` proves no disabled
  mechanism left dangling references — a test, not a manual review;
* :class:`~cooperbench.harness.affordance.Probe` asserts the mechanism actually
  works in-container, so a broken arm fails loudly instead of quietly scoring
  30%.

Usage::

    from cooperbench.harness import Context, team_harness

    spec = team_harness().without("scratchpad")   # cascades to task_mirror
    ctx = Context(run_id="r1", agent_id="agent2", agents=("agent1", "agent2"))

    prompt = spec.prompt(ctx)      # no /workspace/shared references
    runtime = spec.runtime(ctx)    # no volume mount, no CB_TEAM_TASKS_DIR
    assert spec.audit(ctx) == []   # mechanically verified

The phase machine (DEV → REVIEW → SUBMIT) is intentionally not here yet; this
first version is affordances only.
"""

from __future__ import annotations

from cooperbench.harness.affordance import (
    Affordance,
    Context,
    HarnessSpec,
    Leak,
    Probe,
    RuntimeSpec,
)
from cooperbench.harness.team import (
    AUTO_REFRESH,
    CONTAINER_SCRATCHPAD_DIR,
    CONTAINER_TASKS_MIRROR_DIR,
    MCP,
    MESSAGING,
    PROTOCOL,
    SCRATCHPAD,
    TASK_LIST,
    TASK_MIRROR,
    TEAM_AFFORDANCES,
    role_preamble,
    team_harness,
)

__all__ = [
    "AUTO_REFRESH",
    "CONTAINER_SCRATCHPAD_DIR",
    "CONTAINER_TASKS_MIRROR_DIR",
    "MCP",
    "MESSAGING",
    "PROTOCOL",
    "SCRATCHPAD",
    "TASK_LIST",
    "TASK_MIRROR",
    "TEAM_AFFORDANCES",
    "Affordance",
    "Context",
    "HarnessSpec",
    "Leak",
    "Probe",
    "RuntimeSpec",
    "role_preamble",
    "team_harness",
]
