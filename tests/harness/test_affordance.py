"""Tests for the affordance harness.

The headline test is :func:`test_no_disabled_affordance_leaks_into_prompt` —
the mechanical form of the manual audit that found the ``scratchpad`` and
``task_list`` defects in ``team_harness``.  It runs over every single-feature
ablation and both roles, so the bug class cannot regress.
"""

from __future__ import annotations

import itertools

import pytest

from cooperbench.harness import (
    CONTAINER_SCRATCHPAD_DIR,
    CONTAINER_TASKS_MIRROR_DIR,
    TEAM_AFFORDANCES,
    Affordance,
    Context,
    HarnessSpec,
    RuntimeSpec,
    team_harness,
)

ROLES = ("lead", "member")


def ctx(role: str = "member", volume: str = "cb-vol") -> Context:
    return Context(
        run_id="run1",
        agent_id="agent1" if role == "lead" else "agent2",
        agents=("agent1", "agent2"),
        role=role,
        redis_url="redis://localhost:6379#run:run1",
        volume=volume,
    )


# --- the regression guard ------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("off", [a.name for a in TEAM_AFFORDANCES])
def test_no_disabled_affordance_leaks_into_prompt(off: str, role: str) -> None:
    """Disabling a mechanism must remove every trace of it from the prompt."""
    spec = team_harness().without(off)
    assert spec.audit(ctx(role)) == []


@pytest.mark.parametrize("role", ROLES)
def test_audit_clean_for_every_pair_of_ablations(role: str) -> None:
    """Also holds for two-feature ablations, where cascades interact."""
    names = [a.name for a in TEAM_AFFORDANCES]
    for a, b in itertools.combinations(names, 2):
        spec = team_harness().without(a, b)
        assert spec.audit(ctx(role)) == [], f"leak with {a}+{b} off"


def test_audit_catches_a_deliberate_leak() -> None:
    """The audit must actually be able to fail — guard against a vacuous test."""
    leaky = Affordance(
        name="leaky",
        describe=lambda _c: "",
        provision=lambda _c: RuntimeSpec(),
        owns=("coop-send",),  # messaging's artifact, which stays enabled
    )
    spec = HarnessSpec.all_on((*TEAM_AFFORDANCES, leaky)).without("leaky")
    leaks = spec.audit(ctx())
    assert [leak.artifact for leak in leaks] == ["coop-send"]
    assert "leaky" in str(leaks[0])


# --- the specific defects this module exists to prevent ------------------


@pytest.mark.parametrize("role", ROLES)
def test_scratchpad_off_removes_path_and_mount(role: str) -> None:
    """The 62%->30% arm: no volume must mean no instructions to use one."""
    spec = team_harness().without("scratchpad")
    c = ctx(role, volume="")
    assert CONTAINER_SCRATCHPAD_DIR not in spec.prompt(c)
    assert spec.runtime(c).mounts == ()


@pytest.mark.parametrize("role", ROLES)
def test_task_list_off_removes_cli_documentation(role: str) -> None:
    """The 40% arm: an unseeded list must not still be documented."""
    spec = team_harness().without("task_list")
    rendered = spec.prompt(ctx(role))
    for cmd in ("coop-task-create", "coop-task-claim", "coop-task-list"):
        assert cmd not in rendered


def test_scratchpad_off_drops_the_task_mirror_env() -> None:
    """CB_TEAM_TASKS_DIR must not point into an unmounted volume."""
    spec = team_harness().without("scratchpad")
    assert "task_mirror" not in spec.enabled
    assert "CB_TEAM_TASKS_DIR" not in spec.runtime(ctx()).env


def test_task_mirror_present_when_both_dependencies_are() -> None:
    spec = team_harness()
    assert spec.runtime(ctx()).env["CB_TEAM_TASKS_DIR"] == CONTAINER_TASKS_MIRROR_DIR


# --- cascade + validation ------------------------------------------------


def test_disabling_cascades_to_dependents() -> None:
    spec = team_harness().without("messaging")
    for dependent in ("mcp", "protocol"):
        assert dependent not in spec.enabled
    assert spec.audit(ctx()) == []


def test_disabling_task_list_cascades_to_auto_refresh() -> None:
    assert "auto_refresh" not in team_harness().without("task_list").enabled


def test_without_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="unknown affordance"):
        team_harness().without("nope")


def test_spec_rejects_unsatisfied_requirement() -> None:
    with pytest.raises(ValueError, match="requires"):
        HarnessSpec(TEAM_AFFORDANCES, frozenset({"mcp"}))  # mcp needs messaging


def test_enabled_names_must_exist() -> None:
    with pytest.raises(ValueError, match="not in this harness"):
        HarnessSpec(TEAM_AFFORDANCES, frozenset({"ghost"}))


# --- runtime composition -------------------------------------------------


def test_runtime_merges_shared_bus_env_without_conflict() -> None:
    env = team_harness().runtime(ctx()).env
    assert env["CB_TEAM_AGENTS"] == "agent1,agent2"
    assert env["CB_TEAM_ROLE"] == "member"


def test_redis_url_is_rewritten_for_the_container() -> None:
    assert "host.docker.internal" in team_harness().runtime(ctx()).env["CB_TEAM_REDIS_URL"]


def test_conflicting_env_values_are_an_error() -> None:
    a = Affordance("a", lambda _c: "", lambda _c: RuntimeSpec(env={"K": "1"}))
    b = Affordance("b", lambda _c: "", lambda _c: RuntimeSpec(env={"K": "2"}))
    with pytest.raises(ValueError, match="conflicting env"):
        HarnessSpec.all_on((a, b)).runtime(ctx())


def test_full_harness_has_no_leaks_and_probes_the_scratchpad() -> None:
    spec = team_harness()
    assert spec.audit(ctx()) == []
    assert "scratchpad" in dict(spec.probes())


def test_scratchpad_probe_detects_a_missing_volume() -> None:
    """Simulates the container check that would have caught the live defect."""
    probe = dict(team_harness().probes())["scratchpad"]
    assert probe.check(lambda _cmd: 0) is True  # volume present
    assert probe.check(lambda _cmd: 1) is False  # volume missing
