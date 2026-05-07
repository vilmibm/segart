#!/bin/bash
# Run llm_toc_extract.py on a list of items with max-concurrency 2
# (account is tier-limited to 2 concurrent streaming Anthropic connections).
set -u
cd /Users/brewster/tmp/segart
source ~/.anthropic.env

ITEMS=(
  sim_academy-of-management-review_2000-10_25_4
  sim_journal-of-clinical-psychiatry_1983-05_44_5_0
  sim_behavioral-and-brain-sciences_1980-09_3_3
  sim_ans_1978-10_1_1
)

run_one() {
  local item=$1
  local short=${item#sim_}
  short=${short%%_*}
  echo "[$(date +%H:%M:%S)] starting $item" >&2
  python3 llm_toc_extract.py "$item" > "tmp/llm_${short}.log" 2>&1
  local rc=$?
  echo "[$(date +%H:%M:%S)] finished $item rc=$rc" >&2
  return $rc
}

# Pairs at a time
i=0
while [[ $i -lt ${#ITEMS[@]} ]]; do
  j=$((i + 1))
  run_one "${ITEMS[$i]}" &
  P1=$!
  if [[ $j -lt ${#ITEMS[@]} ]]; then
    run_one "${ITEMS[$j]}" &
    P2=$!
    wait $P1 $P2
  else
    wait $P1
  fi
  i=$((i + 2))
done

echo "[$(date +%H:%M:%S)] all done" >&2
