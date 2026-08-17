#!/usr/bin/env bash
# Five-arm flash-50 sweep testing whether the published team ablation measured
# coordination mechanisms or its own instrumentation.
#
#   baseline            full harness — reference point
#   noscratch-legacy    scratchpad off, prompt still describes it   (published: 30%)
#   noscratch-fixed     scratchpad off, prompt routes via messaging
#   notasklist-legacy   task list off, prompt still documents CLI   (published: 40%)
#   notasklist-fixed    task list off, prompt coordinates via messaging
#
# Prediction on the record: the *-fixed arms land near baseline; the *-legacy
# arms reproduce the published drops. If so, the drops are instrumentation.
#
# Arms run sequentially — concurrent arms would contend for CPU and distort
# both wall-clock and (via step timeouts) the results themselves.
set -uo pipefail

MODEL="${MODEL:-gpt-5.6-luna}"
AGENT="${AGENT:-mini_swe_agent_v2}"
SUBSET="${SUBSET:-flash}"
CONC="${CONC:-4}"
PREFIX="${PREFIX:-ablation}"

run_arm() {
  local name="$1"; shift
  local run_name="${PREFIX}-${name}"
  if [ -d "logs/${run_name}" ] && find "logs/${run_name}" -name eval.json | grep -q .; then
    echo "[$(date +%H:%M:%S)] SKIP ${run_name} (already has results)"
    return 0
  fi
  echo "[$(date +%H:%M:%S)] START ${run_name} :: $*"
  uv run cooperbench run -n "${run_name}" -s "${SUBSET}" -m "${MODEL}" -a "${AGENT}" \
      --setting team --backend docker -c "${CONC}" "$@" \
      > "logs/${run_name}.stdout" 2>&1
  echo "[$(date +%H:%M:%S)] DONE  ${run_name} (exit $?)"
}

mkdir -p logs
run_arm baseline
run_arm noscratch-legacy   --team-no-scratchpad --team-legacy-prompt
run_arm noscratch-fixed    --team-no-scratchpad
run_arm notasklist-legacy  --team-no-task-list  --team-legacy-prompt
run_arm notasklist-fixed   --team-no-task-list
echo "[$(date +%H:%M:%S)] SWEEP COMPLETE"
