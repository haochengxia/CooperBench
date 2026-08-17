#!/usr/bin/env bash
# The solo / coop / team triangle on ONE model, harness and eval.
#
# Requires Redis reachable from BOTH the host (runner: redis://localhost) and
# the agent containers (in-container CLI: host.docker.internal -> gateway):
#
#   docker run -d --name cb-redis \
#     -p 127.0.0.1:6379:6379 -p 172.31.255.1:6379:6379 redis:7
#
# Binding loopback only silently kills every coop-send / coop-recv / coop-task-*
# call inside the containers (ConnectionRefused), which does not fail the run —
# it just produces a team that cannot talk. Verify before trusting any result:
#   scripts/check_redis.py <run-name>
set -uo pipefail

MODEL="${MODEL:-gpt-5.6-luna}"
AGENT="${AGENT:-mini_swe_agent_v2}"
SUBSET="${SUBSET:-flash}"
CONC="${CONC:-4}"

run_arm() {
  local name="$1"; shift
  if [ -d "logs/${name}" ] && find "logs/${name}" -name eval.json 2>/dev/null | grep -q .; then
    echo "[$(date +%H:%M:%S)] SKIP ${name}"; return 0
  fi
  echo "[$(date +%H:%M:%S)] START ${name} :: $*"
  uv run cooperbench run -n "${name}" -s "${SUBSET}" -m "${MODEL}" -a "${AGENT}" \
      --backend docker -c "${CONC}" "$@" > "logs/${name}.stdout" 2>&1
  echo "[$(date +%H:%M:%S)] DONE  ${name} (exit $?)"
}

run_arm net-solo    --setting solo
run_arm net-coop    --setting coop
run_arm net-coopgit --setting coop --git
run_arm net-team    --setting team
echo "[$(date +%H:%M:%S)] SETTINGS SWEEP COMPLETE"
