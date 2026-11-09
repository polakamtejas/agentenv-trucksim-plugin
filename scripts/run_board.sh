#!/usr/bin/env bash
# The board's sweep (PLAN.md M11): every eval week of the bundle `trucksim`, once per model, the models one after
# another (agent-env runs four tasks at a time; more would load the rivals' re-planner past the pilot's).
# Each model first answers one call (scripts/check_model.py), so a rejected key or model stops the sweep before it
# spends anything. A model's log is results/logs/board-v11-<model>.log; its summary names each run's instance, which
# `agent-env hf publish --instance` takes.
#
# Usage: scripts/run_board.sh MODEL [MODEL ...]   e.g. scripts/run_board.sh anthropic/claude-haiku-5-5
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo "usage: $0 MODEL [MODEL ...]" >&2; exit 2; }

tasks=()
for f in src/agentenv_trucksim/bundles/trucksim/tasks/ts-v11-*.json; do
  tasks+=(--task "$(basename "$f" .json)")
done
[ "${#tasks[@]}" -eq 30 ] || { echo "expected 15 eval tasks, found $(( ${#tasks[@]} / 2 ))" >&2; exit 1; }

for model in "$@"; do
  .venv/bin/python scripts/check_model.py --model "$model"
done
for model in "$@"; do
  log="results/logs/board-v11-${model//\//_}.log"
  echo "$(date -u +%FT%TZ) $model: 15 weeks -> $log"
  # a failed week doesn't stop the sweep (agent-env exits 1 when any task fails); the log says which
  caffeinate -i .venv/bin/agent-env run trucksim "${tasks[@]}" --model "$model" > "$log" 2>&1 || true
  echo "$(date -u +%FT%TZ) $model: done, $(grep -c 'models: solver' "$log") runs in the summary"
done
