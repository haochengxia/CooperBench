"""The affordance model: a coordination mechanism and its description as one object.

The bug this exists to prevent
------------------------------

In ``team_harness`` a "feature" is a boolean that gates *runtime plumbing*
(``scratchpad_mount_args()``, the task-list pre-seed) while the text telling
the agent that mechanism exists lives in a separate hardcoded string in
``team_harness/prompt.py``.  Nothing ties the two together, so they drift.

Measured on ``main`` (2026-08-16): disabling ``scratchpad`` removes the volume
but leaves six ``/workspace/shared`` references in the prompt, including the
member's export step — which then fails with "No such file or directory".
Disabling ``task_list`` leaves the whole ``coop-task-*`` CLI documented while
the list is never pre-seeded.  Both arms measure prompt/runtime mismatch rather
than the value of the mechanism.

An :class:`Affordance` makes that unrepresentable by bundling three things that
must agree:

``provision``
    what the runtime must supply — env vars, mounts, installed CLIs.
``describe``
    the prompt text.  Only ever emitted when the affordance is enabled,
    because it is reached through the same object.
``owns``
    the artifacts this affordance introduces — paths, command names, env vars.
    A disabled affordance's artifacts must not appear in the prompt; that is
    mechanically checkable, so the drift becomes a test failure rather than a
    silently-invalid experiment arm (see :meth:`HarnessSpec.audit`).

``probe`` is the optional runtime half: a shell command asserting the
affordance actually works inside the container.  A probe on the scratchpad
(``test -w /workspace/shared``) would have caught the live defect the first
time the arm ran.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol


@dataclass(frozen=True)
class Context:
    """Everything an affordance needs to describe or provision itself.

    Deliberately a plain value object: affordances are pure functions of this,
    which is what makes :meth:`HarnessSpec.audit` able to render every prompt
    variant without standing up Redis, Docker, or a run.
    """

    run_id: str
    agent_id: str
    agents: tuple[str, ...]
    role: str = "member"
    redis_url: str = ""
    volume: str = ""

    @property
    def lead(self) -> str:
        return self.agents[0] if self.agents else ""

    @property
    def partners(self) -> tuple[str, ...]:
        return tuple(a for a in self.agents if a != self.agent_id)

    @property
    def is_lead(self) -> bool:
        return self.role == "lead"


@dataclass(frozen=True)
class RuntimeSpec:
    """What an affordance needs from the container.

    Merged across enabled affordances by :meth:`HarnessSpec.runtime`.
    """

    env: dict[str, str] = field(default_factory=dict)
    mounts: tuple[str, ...] = ()
    installs: tuple[str, ...] = ()

    def merge(self, other: RuntimeSpec) -> RuntimeSpec:
        """Combine two specs.  Conflicting env keys are an error, not a
        last-write-wins — two affordances disagreeing about a variable is a
        design bug we want surfaced loudly."""
        clash = {k for k in self.env.keys() & other.env.keys() if self.env[k] != other.env[k]}
        if clash:
            raise ValueError(f"conflicting env values for {sorted(clash)}")
        return RuntimeSpec(
            env={**self.env, **other.env},
            mounts=self.mounts + other.mounts,
            installs=self.installs + tuple(i for i in other.installs if i not in self.installs),
        )


@dataclass(frozen=True)
class Probe:
    """An in-container assertion that an affordance really works.

    ``command`` is run with the container's shell; a zero exit means healthy.
    """

    command: str
    expect_exit: int = 0

    def check(self, run: Callable[[str], int]) -> bool:
        return run(self.command) == self.expect_exit


class Describe(Protocol):
    def __call__(self, ctx: Context) -> str: ...


class Provision(Protocol):
    def __call__(self, ctx: Context) -> RuntimeSpec: ...


@dataclass(frozen=True)
class Affordance:
    """One coordination mechanism: capability, description, and artifacts.

    Args:
        name: stable identifier, also the ablation flag name.
        describe: returns the prompt text.  Return ``""`` when the affordance
            has nothing to say for this context (e.g. role-specific text).
        provision: returns what the container needs.
        owns: artifacts this affordance introduces — command names, paths, env
            var names.  Used by :meth:`HarnessSpec.audit` to prove a disabled
            affordance left no dangling references in the prompt.
        probe: optional in-container health check.
        requires: names of affordances this one depends on.  Enabling this
            without them is a configuration error.
    """

    name: str
    describe: Describe
    provision: Provision
    owns: tuple[str, ...] = ()
    probe: Probe | None = None
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Leak:
    """A disabled affordance's artifact found in the rendered prompt."""

    affordance: str
    artifact: str
    role: str

    def __str__(self) -> str:
        return f"{self.affordance!r} is disabled but its artifact {self.artifact!r} appears in the {self.role} prompt"


@dataclass(frozen=True)
class HarnessSpec:
    """A set of affordances plus which of them are enabled.

    The whole point: ``prompt()`` and ``runtime()`` read from the same
    ``enabled`` set, so a mechanism cannot be described without being
    provisioned, or provisioned without being described.
    """

    affordances: tuple[Affordance, ...]
    enabled: frozenset[str]

    def __post_init__(self) -> None:
        names = {a.name for a in self.affordances}
        unknown = self.enabled - names
        if unknown:
            raise ValueError(f"enabled names not in this harness: {sorted(unknown)}")
        for a in self.affordances:
            if a.name in self.enabled:
                missing = [r for r in a.requires if r not in self.enabled]
                if missing:
                    raise ValueError(f"{a.name!r} requires {missing}, which are disabled")

    @classmethod
    def all_on(cls, affordances: tuple[Affordance, ...]) -> HarnessSpec:
        return cls(affordances, frozenset(a.name for a in affordances))

    def without(self, *names: str) -> HarnessSpec:
        """The ablation constructor.  Returns a spec with ``names`` disabled.

        Because prompt text is reached through the affordance, disabling here
        removes the description too — no second edit, no drift.

        Disabling **cascades**: anything declaring a disabled affordance in its
        ``requires`` is disabled too.  Concretely, the task-list→scratchpad
        mirror cannot outlive the scratchpad it writes into — which is exactly
        the discipline missing today, where ``CB_TEAM_TASKS_DIR`` stays set to
        a path under an unmounted volume.
        """
        known = {a.name for a in self.affordances}
        unknown = set(names) - known
        if unknown:
            raise ValueError(f"unknown affordance(s) {sorted(unknown)}; known: {sorted(known)}")
        enabled = self.enabled - set(names)
        # Fixed point: drop anything whose requirements are no longer met.
        while True:
            survivors = {
                a.name for a in self.affordances if a.name in enabled and all(r in enabled for r in a.requires)
            }
            if survivors == enabled:
                return replace(self, enabled=frozenset(enabled))
            enabled = survivors

    def active(self) -> tuple[Affordance, ...]:
        return tuple(a for a in self.affordances if a.name in self.enabled)

    def disabled(self) -> tuple[Affordance, ...]:
        return tuple(a for a in self.affordances if a.name not in self.enabled)

    def prompt(self, ctx: Context) -> str:
        """Concatenate the descriptions of every enabled affordance."""
        blocks = [text for a in self.active() if (text := a.describe(ctx).strip())]
        return "\n\n".join(blocks)

    def runtime(self, ctx: Context) -> RuntimeSpec:
        spec = RuntimeSpec()
        for a in self.active():
            spec = spec.merge(a.provision(ctx))
        return spec

    def probes(self) -> tuple[tuple[str, Probe], ...]:
        return tuple((a.name, a.probe) for a in self.active() if a.probe is not None)

    def audit(self, ctx: Context) -> list[Leak]:
        """Prove no disabled affordance leaks artifacts into the prompt.

        This is the mechanical version of the manual check that found the
        scratchpad and task_list defects.  Wire it into the test suite and the
        bug class cannot regress.
        """
        rendered = self.prompt(ctx)
        return [
            Leak(affordance=a.name, artifact=artifact, role=ctx.role)
            for a in self.disabled()
            for artifact in a.owns
            if artifact in rendered
        ]
