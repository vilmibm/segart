#!/bin/bash
# Run llm_toc_extract.py on 5 items × 2 models (Opus 4.7, Sonnet 4.6),
# respecting the 2-concurrent streaming connection rate limit.
set -u
cd /Users/brewster/tmp/segart
source ~/.anthropic.env

ITEMS=(
  sim_journal-of-research-in-reading_1987-02_10_1
  sim_southern-journal-of-philosophy_summer-1976_14_2
  sim_journal-of-chemical-ecology_1977-03_3_2
  sim_journal-american-academy-child-adolescent-psychiatry_2013-06_52_6
  sim_pediatrics_1977-09_60_3
)

MODELS=(
  claude-opus-4-7
  claude-sonnet-4-6
)

run_one() {
  local item=$1 model=$2
  local model_short
  case "$model" in
    claude-opus-4-7)   model_short=opus;;
    claude-sonnet-4-6) model_short=sonnet;;
    *) model_short=$(echo "$model" | tr -d '-');;
  esac
  local out="tmp/tocs/${item}_toc_llm_${model_short}.json"
  local log="tmp/llm_${model_short}_$(echo "$item" | head -c 30).log"
  echo "[$(date +%H:%M:%S)] start $model_short $item" >&2
  python3 llm_toc_extract.py "$item" \
    --model "$model" \
    --out "$out" > "$log" 2>&1
  local rc=$?
  echo "[$(date +%H:%M:%S)] done  $model_short $item rc=$rc" >&2
  return $rc
}

# Run in pairs (one Opus + one Sonnet at a time, 2 concurrent max)
for item in "${ITEMS[@]}"; do
  run_one "$item" "${MODELS[0]}" &
  P1=$!
  run_one "$item" "${MODELS[1]}" &
  P2=$!
  wait $P1 $P2
done

echo "[$(date +%H:%M:%S)] all 10 runs complete" >&2
