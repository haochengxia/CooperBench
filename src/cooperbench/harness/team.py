"""Team-mode coordination mechanisms expressed as affordances.

A faithful re-expression of ``team_harness``'s five ablation flags, plus the
task-list→scratchpad mirror that today is set unconditionally and therefore
points into an unmounted volume whenever the scratchpad is off.

What changes versus ``team_harness``:

* prompt text lives next to the thing it describes, so ablating a mechanism
  removes its description (the defect this module exists to prevent);
* ``CB_TEAM_TASKS_DIR`` is owned by ``task_mirror``, which ``requires`` both
  ``task_list`` and ``scratchpad`` — disabling either drops the env var
  instead of leaving it dangling;
* the scratchpad carries a :class:`~cooperbench.harness.affordance.Probe`
  that fails loudly if the volume is missing.

The lead/member split is deliberately *not* an affordance: without it team
mode is just coop mode, so it is a property of the harness rather than a
toggle — same call as ``TeamHarnessConfig``.
"""

from __future__ import annotations

from cooperbench.harness.affordance import (
    Affordance,
    Context,
    HarnessSpec,
    Probe,
    RuntimeSpec,
)

CONTAINER_SCRATCHPAD_DIR = "/workspace/shared"
CONTAINER_TASKS_MIRROR_DIR = f"{CONTAINER_SCRATCHPAD_DIR}/tasks"


def _rewrite_url_for_container(url: str) -> str:
    """Host→container Redis URL rewrite (mirrors ``TeamSession``)."""
    if not url:
        return url
    for needle in ("//localhost", "//127.0.0.1"):
        if needle in url:
            return url.replace(needle, "//host.docker.internal", 1)
    return url


def _bus_env(ctx: Context) -> dict[str, str]:
    """Env every Redis-backed mechanism needs.  Identical values across
    affordances, so ``RuntimeSpec.merge`` accepts the overlap."""
    return {
        "CB_TEAM_REDIS_URL": _rewrite_url_for_container(ctx.redis_url),
        "CB_TEAM_RUN_ID": ctx.run_id,
        "CB_TEAM_AGENT_ID": ctx.agent_id,
        "CB_TEAM_AGENTS": ",".join(ctx.agents),
        "CB_TEAM_ROLE": ctx.role,
    }


# --- messaging ----------------------------------------------------------


def _describe_messaging(ctx: Context) -> str:
    partners = ", ".join(f"`{p}`" for p in ctx.partners)
    return f"""## Messaging

You are **{ctx.agent_id}**, working alongside {partners}.

```bash
coop-send <recipient> "text"   # message one peer
coop-broadcast "text"          # message every peer
coop-recv                      # drain your inbox (JSON list)
coop-peek                      # count unread
```

Your peers only know what you tell them.  Prefer symbol names and diffs over
line numbers — line references go stale as soon as either of you edits."""


MESSAGING = Affordance(
    name="messaging",
    describe=_describe_messaging,
    provision=lambda ctx: RuntimeSpec(env=_bus_env(ctx), installs=("coop-msg",)),
    owns=("coop-send", "coop-broadcast", "coop-recv", "coop-peek"),
)


# --- task list ----------------------------------------------------------


def _describe_task_list(ctx: Context) -> str:
    organiser = (
        "Break the work into tasks with `coop-task-create` before coding, then track progress."
        if ctx.is_lead
        else "Claim what the lead has queued for you, then report progress."
    )
    return f"""## Shared task list

{organiser}

```bash
coop-task-create "<title>"                  # prints task_id
coop-task-create --assign <agent> "<title>"  # pre-assign (owner must still claim)
coop-task-claim <task_id>                    # exit 0 if yours, 2 if lost the race
coop-task-update <task_id> <status> [-n "<note>"]
                                             # open | in_progress | blocked | done
coop-task-list [--mine] [--open]             # JSON
```"""


TASK_LIST = Affordance(
    name="task_list",
    describe=_describe_task_list,
    provision=lambda ctx: RuntimeSpec(env=_bus_env(ctx), installs=("coop-task",)),
    owns=("coop-task-create", "coop-task-claim", "coop-task-update", "coop-task-list"),
)


# --- scratchpad ---------------------------------------------------------


def _describe_scratchpad(ctx: Context) -> str:
    use = (
        "Collect member patches here and integrate them."
        if ctx.is_lead
        else f"Export your diff for the lead:\n\n```bash\ncd /workspace/repo && git diff > {CONTAINER_SCRATCHPAD_DIR}/{ctx.agent_id}.patch\n```"
    )
    return f"""## Shared scratchpad

A shared volume is mounted at `{CONTAINER_SCRATCHPAD_DIR}/` — visible to every
agent, and NOT evaluated.  {use}"""


def _provision_scratchpad(ctx: Context) -> RuntimeSpec:
    if not ctx.volume:
        return RuntimeSpec()
    return RuntimeSpec(mounts=("--volume", f"{ctx.volume}:{CONTAINER_SCRATCHPAD_DIR}"))


SCRATCHPAD = Affordance(
    name="scratchpad",
    describe=_describe_scratchpad,
    provision=_provision_scratchpad,
    owns=(CONTAINER_SCRATCHPAD_DIR,),
    # The check that would have caught the 62%->30% defect on its first run.
    probe=Probe(command=f"test -d {CONTAINER_SCRATCHPAD_DIR} && test -w {CONTAINER_SCRATCHPAD_DIR}"),
)


# --- task-list mirror (joint product of the two above) ------------------


def _describe_task_mirror(_ctx: Context) -> str:
    return f"""The task list is also mirrored to `{CONTAINER_TASKS_MIRROR_DIR}/`,
so `ls {CONTAINER_TASKS_MIRROR_DIR}/` works without the CLI."""


TASK_MIRROR = Affordance(
    name="task_mirror",
    describe=_describe_task_mirror,
    provision=lambda _ctx: RuntimeSpec(env={"CB_TEAM_TASKS_DIR": CONTAINER_TASKS_MIRROR_DIR}),
    owns=("CB_TEAM_TASKS_DIR", CONTAINER_TASKS_MIRROR_DIR),
    requires=("task_list", "scratchpad"),
)


# --- MCP / auto-refresh / protocol --------------------------------------


def _describe_mcp(_ctx: Context) -> str:
    return """## Blocking receive

`wait_for_message` is registered as an MCP tool — call it to block until a
peer messages you instead of polling `coop-recv`."""


MCP = Affordance(
    name="mcp",
    describe=_describe_mcp,
    provision=lambda ctx: RuntimeSpec(env=_bus_env(ctx)),
    owns=("wait_for_message",),
    requires=("messaging",),
)


AUTO_REFRESH = Affordance(
    name="auto_refresh",
    # No prompt text by design: the poller injects task-list state into the
    # agent loop without the agent asking, so there is nothing to instruct.
    describe=lambda _ctx: "",
    provision=lambda ctx: RuntimeSpec(env=_bus_env(ctx)),
    owns=(),
    requires=("task_list",),
)


def _describe_protocol(_ctx: Context) -> str:
    return """## Typed requests

```bash
coop-task-request <recipient> <kind> "<body>"   # ask for something specific
coop-task-respond <request_id> "<body>"          # answer one
```"""


PROTOCOL = Affordance(
    name="protocol",
    describe=_describe_protocol,
    provision=lambda ctx: RuntimeSpec(env=_bus_env(ctx), installs=("coop-task",)),
    owns=("coop-task-request", "coop-task-respond"),
    requires=("messaging",),
)


TEAM_AFFORDANCES: tuple[Affordance, ...] = (
    MESSAGING,
    TASK_LIST,
    SCRATCHPAD,
    TASK_MIRROR,
    MCP,
    AUTO_REFRESH,
    PROTOCOL,
)


def team_harness() -> HarnessSpec:
    """The full team harness, every affordance enabled.

    Ablate with :meth:`HarnessSpec.without`::

        team_harness().without("scratchpad")   # also drops task_mirror
    """
    return HarnessSpec.all_on(TEAM_AFFORDANCES)


def role_preamble(ctx: Context) -> str:
    """The lead/member framing — a property of team mode, not an affordance."""
    if ctx.is_lead:
        members = ", ".join(f"`{m}`" for m in ctx.partners)
        return (
            f"## You are the team-lead\n\nYou are **{ctx.agent_id}**; your members are {members}.\n"
            "Organise the work, then integrate every member's contribution into your own tree "
            "before submitting — the bench scores your patch, so it must contain the whole team's work."
        )
    return (
        f"## You are a team member\n\nYou are **{ctx.agent_id}**; the team-lead is **{ctx.lead}**.\n"
        "Implement what you own and hand it back to the lead."
    )
