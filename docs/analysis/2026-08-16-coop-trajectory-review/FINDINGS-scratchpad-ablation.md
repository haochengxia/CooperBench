# The `--team-no-scratchpad` ablation measures a broken pipeline, not a removed affordance

**Date:** 2026-08-16 · **Severity:** invalidates a published number · **Status:** confirmed by live check

## The number in question

From the team-harness ablation report (flash-50, gpt-5.5):

| configuration | pass rate |
|---|---|
| team — all features (baseline) | 62% |
| **team — no scratchpad** | **30%** |
| solo | 48% |

A 32-point drop from removing one shared directory — larger than the effect of
removing the task list (40%), MCP (60%), or auto-refresh (60%). That reads as
"shared memory is the load-bearing coordination affordance."

It is not what the flag does.

## What `--team-no-scratchpad` actually does

Disabling the feature removes the Docker volume — and nothing else. Every
instruction telling agents to *use* that volume stays in the prompt, and the
env var pointing at it stays set.

Live check on `main`:

```
$ config.scratchpad = False
mount args with scratchpad OFF: []          # volume not mounted
prompt mentions /workspace/shared: True
occurrences: 6                              # ...but the prompt still says to use it
CB_TEAM_TASKS_DIR = /workspace/shared/tasks # ...and the env var still points there
```

The six surviving references, from the member prompt:

```
The lead may have left a plan at `/workspace/shared/PLAN.md`.  Read it
`ls /workspace/shared/tasks/` to browse without the CLI.
Read `/workspace/shared/PLAN.md` if it exists, then implement.
cd /workspace/repo && git diff > /workspace/shared/agent2.patch
`coop-task-update <task_id> done -n "patch at /workspace/shared/agent2.patch"`.  The lead will integrate.
A shared scratchpad volume is mounted at `/workspace/shared/` for coordination
```

## Why this is not a valid ablation

The member→lead handoff is *defined* in terms of the scratchpad. With the
volume gone the directory does not exist at all, so the export command the
prompt issues **hard-fails**:

```
$ docker run --rm --entrypoint bash akhatua/cooperbench-go-chi:task26 \
    -c 'git diff > /workspace/shared/agent2.patch; echo "exit=$?"; ls -d /workspace/shared'
bash: line 1: /workspace/shared/agent2.patch: No such file or directory
redirect exit=1
ls: /workspace/shared: No such file or directory

# same image, volume mounted (baseline arm):
redirect exit=0
-rw-r--r-- 1 root root 0 Aug 16 18:14 agent2.patch
```

So with the volume gone:

1. Members are told to export their diff to `/workspace/shared/<id>.patch` —
   the redirect fails outright (exit 1); nothing is written anywhere.
2. Members are told "the lead will integrate" — the lead has nothing to
   integrate from.
3. The lead is told to `ls /workspace/shared/` for member-produced patches —
   it will be empty or absent.
4. Members are told to read `/workspace/shared/PLAN.md` — never present.
5. `coop-task-list`'s filesystem mirror still targets `CB_TEAM_TASKS_DIR`
   under the unmounted path (`coop_task.py:312-317`); the write fails and is
   swallowed (`except OSError: pass`).

So the arm does not test "a team without shared memory." It tests **a team
instructed to use a dead drop** — one whose primary integration mechanism is
described in the prompt and does not exist at runtime. Wasted turns writing to
nowhere and reading empty directories are part of the measured cost.

The honest reading of 62% → 30% is *prompt/runtime mismatch*, not *value of
shared memory*. And since team-mode grading depends on the lead's patch
containing member work, breaking the handoff plausibly accounts for most of
the drop on its own.

## Root cause

`build_team_instruction` has no access to the harness config:

```python
# src/cooperbench/team_harness/prompt.py:180
def build_team_instruction(task, *, agents, agent_id, team_role, git_enabled=False)
```

`TeamSession.prompt_for` (`team_harness/__init__.py:241`) passes no config
through, and `_lead_block` / `_member_block` hardcode the scratchpad text.
`build_team_env` (`team_harness/runtime.py:30-36`) sets `CB_TEAM_TASKS_DIR`
unconditionally. Only `scratchpad_mount_args()` consults `config.scratchpad`.

## The same bug affects `task_list` — and that reframes the whole table

Auditing all five flags (disable each, render both role prompts, check whether
the feature's own artifacts survive):

| flag | leaks when disabled | published arm |
|---|---|---|
| `scratchpad` | **yes** — `/workspace/shared` ×6 | **30%** |
| `task_list` | **yes** — the whole `coop-task-*` CLI, both roles | **40%** |
| `mcp` | no | 60% |
| `auto_refresh` | no | 60% |
| `protocol` | no | 70% |

4 of 10 (feature, role) combinations leak. With `task_list` off the list is
never pre-seeded (`runner/team.py:138`) but the CLI is still installed
(`config.task_list or config.protocol`) and still documented — so members are
told "run `coop-task-list --open` to see what needs doing; if your `agent_id`
appears as a pre-assigned `owner`…" against a list nobody seeded.

**The separation is perfect: the two arms with the coupling bug are the two
arms that dropped below solo; the three clean arms all landed at or above
baseline.** The magnitude of each "drop" tracks whether the arm is
instrumented correctly, not how load-bearing the mechanism is. The conclusion
that scratchpad and task list are the harness's load-bearing features is not
supported by these runs.

## Fix shipped

`src/cooperbench/harness/` re-expresses each mechanism as an **affordance**
that owns its capability, its prompt text, and the artifacts it introduces, so
the three cannot drift:

```
ablation arm            role    team_harness (current)    harness/ (new)
----------------------------------------------------------------------
--scratchpad            lead    LEAKS 1 artifact(s)       clean
--scratchpad            member  LEAKS 1 artifact(s)       clean
--task-list             lead    LEAKS 4 artifact(s)       clean
--task-list             member  LEAKS 4 artifact(s)       clean
```

Three properties do the work:

- **prompt and capability are one object** — `spec.without("scratchpad")`
  removes the description because it is reached through the same affordance;
- **`owns` makes the audit mechanical** — `HarnessSpec.audit()` is run as a
  test over every single- and two-feature ablation × both roles, so this bug
  class cannot regress (`tests/harness/test_affordance.py`, 33 tests);
- **`requires` cascades** — `task_mirror` (which sets `CB_TEAM_TASKS_DIR`)
  depends on both `task_list` and `scratchpad`, so disabling either drops the
  env var instead of leaving it pointed at an unmounted volume.

The scratchpad also carries a `Probe` (`test -d /workspace/shared && test -w
/workspace/shared`) — the container-side assertion that would have failed
loudly the first time this arm ran, instead of quietly returning 30%.

## Suggested fix for `team_harness` itself

Thread `TeamHarnessConfig` into `build_team_instruction` and make each
prompt block conditional on the feature it describes. When `scratchpad` is
off, the member block should route the handoff through a channel that still
exists (`coop-send` with the diff inline, or the git remote when `--git` is
on) rather than through a path that doesn't. `build_team_env` should omit
`CB_TEAM_TASKS_DIR` when the scratchpad is disabled so the mirror is skipped
rather than failing silently.

Then re-run the ablation arm. Until then the 30% figure should not be cited as
evidence for the value of shared memory.

## Why it matters beyond one number

This arm is the main published evidence that a shared-state substrate is what
makes team mode beat coop. Our trajectory analysis
([README.md](README.md)) independently found that the substrate story is
weaker than it looks — the failures that dominate are semantic, not
coordination-substrate failures. If the 30% is an artifact, those two results
agree, and the case for "shared memory is the key affordance" rests on
considerably less than it appears to.
