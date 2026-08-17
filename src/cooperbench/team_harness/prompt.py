"""Prompt assembly for team mode.

Builds on the shared coop prompt (``cooperbench.agents._coop.prompt``)
by appending a role-specific team block:

  - ``lead`` block: names the agent ``team-lead``, lists members,
    documents the ``coop-task-*`` CLI from an organizer's perspective,
    points to the shared scratchpad.
  - ``member`` block: names the lead, encourages claiming open tasks,
    documents the same CLI from a worker's perspective.

A "team of one" is degenerate and falls back to a plain solo prompt
(no team block emitted).
"""

from __future__ import annotations

from cooperbench.agents._coop.prompt import build_instruction as _build_coop_instruction

_TEAM_LIST_USAGE = """Available shell commands (Redis-backed, atomic):

```bash
coop-task-create "<title>"                 # creates an open, unassigned task; prints task_id
coop-task-create --assign <agent> "<title>" # creates and pre-assigns; agent must still claim
coop-task-claim <task_id>                   # exit 0 if you got it, 2 if someone else owns it
coop-task-update <task_id> <status> [-n "<note>"]
                                            # statuses: open | in_progress | blocked | done
coop-task-list                              # JSON list of every task in the team
coop-task-list --mine                       # tasks you own
coop-task-list --open                       # open, unassigned tasks
```

Messaging (`coop-send` / `coop-recv` / `coop-broadcast` / `coop-peek`)
also works and is the right tool for short questions that don't
warrant a new task."""

_MESSAGING_ONLY_USAGE = """Available shell commands (Redis-backed):

```bash
coop-send <recipient> "text"   # message one teammate
coop-broadcast "text"          # message the whole team
coop-recv                      # drain your inbox (JSON list)
coop-peek                      # count unread
```

There is no shared task list in this configuration — coordinate entirely
through messages."""


def _handoff_lead(scratchpad: bool) -> tuple[str, str]:
    """(checklist item, how-to) for collecting member work.

    With the scratchpad off there is no shared filesystem, so the handoff has
    to route through the channel that still exists — Redis messaging.  The old
    prompt kept pointing at ``/workspace/shared`` regardless, which is the
    defect the ``--team-no-scratchpad`` arm actually measured.
    """
    if scratchpad:
        return (
            "- [ ] Every member has dropped their patch at\n"
            "      `/workspace/shared/<agent_id>.patch`.  (Run\n"
            "      `ls /workspace/shared/*.patch` to verify.)\n"
            "- [ ] You have read each member's patch (`cat /workspace/shared/<agent>.patch`)\n"
            "      and applied it to your working tree (`git apply\n"
            "      /workspace/shared/<agent>.patch`, fixing any conflicts\n"
            "      manually with Edit until the tree builds).",
            "Drop design notes, interface contracts, or member-produced patches "
            "in `/workspace/shared/` so the whole team can see them with "
            "`ls /workspace/shared/`.",
        )
    return (
        "- [ ] Every member has sent you their diff with `coop-send`.\n"
        "      (Run `coop-recv` to drain your inbox and collect them.)\n"
        "- [ ] You have applied each member's diff to your working tree\n"
        "      (save it to a local file and `git apply`, fixing any conflicts\n"
        "      manually with Edit until the tree builds).",
        "There is no shared filesystem in this configuration — `coop-send` / "
        "`coop-recv` is the only channel between you and your members.",
    )


def _lead_block(agent_id: str, members: list[str], *, scratchpad: bool = True, task_list: bool = True) -> str:
    member_list = ", ".join(f"`{m}`" for m in members)
    collect_item, channel_note = _handoff_lead(scratchpad)
    plan_step = (
        "Drop a one-paragraph plan in\n   `/workspace/shared/PLAN.md` so members see your decomposition."
        if scratchpad
        else "Send a one-paragraph plan to every member with\n   `coop-broadcast` so they see your decomposition."
    )
    assign_step = (
        '2. Break the spec into 2-4 concrete tasks and assign one per member\n   with `coop-task-create --assign <agent> "..."`.\n'
        "3. You **may also implement a task yourself** in parallel.\n"
        "4. Poll progress: `coop-task-list` periodically.  If a member's task\n"
        "   is `blocked`, read the `last_note` and either reassign or\n"
        "   unblock it with `coop-send`."
        if task_list
        else "2. Break the spec into 2-4 concrete tasks and tell each member which\n"
        "   one they own with `coop-send`.\n"
        "3. You **may also implement a task yourself** in parallel.\n"
        "4. Poll progress by asking members with `coop-send`; drain replies\n"
        "   with `coop-recv`."
    )
    done_item = (
        "- [ ] `coop-task-list` shows every team task as `done`."
        if task_list
        else "- [ ] Every member has told you (via `coop-send`) that their work is done."
    )
    usage = f"\n{_TEAM_LIST_USAGE}\n" if task_list else "\n" + _MESSAGING_ONLY_USAGE + "\n"
    return f"""## You are the team-lead

You are **{agent_id}** — the team-lead.  Members reporting to you:
{member_list}.

### The integration step is the WHOLE point of your job — DO NOT SKIP IT

The benchmark scores the **merged** team output, not your individual
contribution.  If you submit `patch.txt` containing only your own
feature, the team **fails** — even if your feature works perfectly.
The single most common failure mode here is "lead ran `git diff > patch.txt`
before pulling in the member's work."  Avoid this.

Workflow:

1. **Plan to avoid conflicts.** Read the feature spec.  If features
   touch the same file/function, divide the work so changes occupy
   disjoint regions (e.g., one feature owns the function signature,
   the other owns helper additions).  {plan_step}
{assign_step}

### Before submitting — MANDATORY integration checklist

You may submit ONLY after **all five** of these are true:

{done_item}
{collect_item}
- [ ] The merged tree builds / compiles.  Run the project's build
      or import test before continuing.
- [ ] `git diff` now shows BOTH features — your own and every
      member's — present in the working tree.

Only when ALL five boxes are checked, write `/workspace/repo/patch.txt`
via `git diff` and exit — this is REQUIRED:

```bash
cd /workspace/repo && git diff > patch.txt && wc -l patch.txt
```

If `wc -l patch.txt` looks too small to contain everyone's work, you
skipped the integration step — go back to step 3 of the checklist.
{usage}
{channel_note}"""


def _member_block(agent_id: str, lead: str, *, scratchpad: bool = True, task_list: bool = True) -> str:
    plan_note = (
        "The lead may have left a plan at `/workspace/shared/PLAN.md`.  Read it\n"
        "first — it tells you which file regions your feature owns, vs. those\n"
        "reserved for your peers.  Edit ONLY the regions your task owns."
        if scratchpad
        else "The lead will message you a plan — run `coop-recv` first.  It tells you\n"
        "which file regions your feature owns, vs. those reserved for your\n"
        "peers.  Edit ONLY the regions your task owns."
    )
    if task_list:
        browse = "  You can also `ls\n   /workspace/shared/tasks/` to browse without the CLI." if scratchpad else ""
        claim_steps = (
            f"1. Run `coop-task-list --open` to see what needs doing.  If your\n"
            f"   `agent_id` appears as a pre-assigned `owner` on a task, that's the\n"
            f"   one the lead expects you to take.{browse}\n"
            f"2. `coop-task-claim <task_id>`.  If you lose the race (exit code 2),\n"
            f"   pick another.\n"
            f"3. Implement, staying within your region.  When you hit a blocker, run\n"
            f'   `coop-task-update <task_id> blocked -n "<what you need>"` and\n'
            f'   `coop-send {lead} "blocked on task <id>: ..."`.'
        )
        done_note = '   Then `coop-task-update <task_id> done -n "<where your work is>"`.  The lead will integrate.'
    else:
        claim_steps = (
            f"1. Run `coop-recv` to see what the lead has assigned you.\n"
            f'2. Confirm what you are taking with `coop-send {lead} "taking: ..."`.\n'
            f"3. Implement, staying within your region.  When you hit a blocker,\n"
            f'   `coop-send {lead} "blocked: <what you need>"`.'
        )
        done_note = f'   Then `coop-send {lead} "done: <what you implemented>"`.  The lead will integrate.'
    export = (
        f"   ```bash\n   cd /workspace/repo && git diff > /workspace/shared/{agent_id}.patch\n   ```"
        if scratchpad
        else f"   ```bash\n   cd /workspace/repo && git diff | coop-send {lead}\n   ```\n"
        f"   (`coop-send` reads the diff from stdin.)"
    )
    usage = _TEAM_LIST_USAGE if task_list else _MESSAGING_ONLY_USAGE
    return f"""## You are a team member

You are **{agent_id}**.  The team-lead is **{lead}**, who will
organize work and tell you what to take.

### Stay in your lane

{plan_note}

Workflow:

{claim_steps}
4. When done, ALWAYS export your diff for the lead to consume:

{export}

{done_note}

### Final submission — REQUIRED

The bench scores per-agent.  Before exiting you MUST write your own
`/workspace/repo/patch.txt`:

```bash
cd /workspace/repo && git diff > patch.txt && wc -l patch.txt
```

Your `/workspace/repo/patch.txt` should reflect your final working
tree.  If the lead asked you to merge in their plan, do so first.

{usage}

{
        "A shared scratchpad volume is mounted at `/workspace/shared/` for "
        "coordination — files there are NOT evaluated.  Use it for anything "
        "your peers might need to see — partial diffs, interface sketches, "
        "error logs from your reproduction script."
        if scratchpad
        else "There is no shared filesystem in this configuration — `coop-send` is "
        "the only way to show your peers anything."
    }"""


def team_task_section(
    *,
    agents: list[str] | None,
    agent_id: str | None,
    team_role: str | None,
    scratchpad: bool = True,
    task_list: bool = True,
) -> str:
    """Return JUST the team-task-list section for an adapter to append.

    Used by Python-loop adapters that already have their own coop
    prompts covering messaging / git / submission, but need to teach
    the LLM about the new ``coop-task-*`` CLI + role split without
    re-explaining everything else.  CLI adapters use the bigger
    ``build_team_instruction`` instead.

    Empty string when team mode isn't active (no role, <2 agents).
    """
    if not team_role or not agents or not agent_id or len(agents) < 2:
        return ""
    members = [a for a in agents if a != agent_id]
    if team_role == "lead":
        return _lead_block(agent_id, members, scratchpad=scratchpad, task_list=task_list)
    lead = members[0] if members else "team-lead"
    return _member_block(agent_id, lead, scratchpad=scratchpad, task_list=task_list)


def build_team_instruction(
    task: str,
    *,
    agents: list[str] | None,
    agent_id: str | None,
    team_role: str | None,
    git_enabled: bool = False,
    scratchpad: bool = True,
    task_list: bool = True,
) -> str:
    """Compose the full instruction for a team-mode agent run.

    Args:
        task: Raw feature spec.
        agents: All agent ids in the team.  A team of one falls back to
            solo (no team block).
        agent_id: This agent's id.  Required when ``team_role`` is set.
        team_role: ``"lead"`` or ``"member"``.  ``None`` means we're not
            in team mode — falls back to the coop / solo prompt.
        git_enabled: When True, the shared coop+git block is appended
            (same as coop mode).
        scratchpad: Whether the ``/workspace/shared`` volume is actually
            mounted.  When False the prompt routes the member→lead handoff
            through messaging instead of describing a directory that does
            not exist.
        task_list: Whether the Redis task list is seeded and available.
            When False the ``coop-task-*`` CLI is not documented and
            coordination is described through messaging.

    ``scratchpad`` / ``task_list`` MUST track the runtime config.  They
    default to True because that is the full harness; passing the real
    values is what keeps an ablation arm measuring the mechanism rather
    than a prompt describing plumbing that was removed.

    Returns the assembled prompt.  Team mode injects its own block
    INSTEAD of the regular coop messaging block — the coop-task CLI
    is the primary coordination primitive in team mode; `coop-send`
    is documented inside the team block as the secondary channel.
    """
    # Base prompt = task + submission protocol (no coop block — we
    # provide our own).
    base = _build_coop_instruction(task)

    if not team_role or not agents or not agent_id or len(agents) < 2:
        return base

    members = [a for a in agents if a != agent_id]
    if team_role == "lead":
        team_section = _lead_block(agent_id, members, scratchpad=scratchpad, task_list=task_list)
    else:  # member
        # Pick the first non-self as the implied lead.  The runner always
        # sets agents=[lead, member1, member2, ...] so this is correct.
        lead = members[0] if members else "team-lead"
        # If the caller passed a specific lead via members[0], honour it.
        team_section = _member_block(agent_id, lead, scratchpad=scratchpad, task_list=task_list)

    sections = [base, team_section]
    if git_enabled:
        from cooperbench.agents._coop.prompt import _git_block

        sections.append(_git_block(agent_id, members))
    return "\n\n---\n\n".join(sections)
