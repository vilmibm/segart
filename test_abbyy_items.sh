#!/bin/bash
# Run docling-with-tables on a few ABBYY periodical items for comparison.
set -e
ITEMS=/Users/brewster/tmp/segart/tmp/items
SCRIPT=/Users/brewster/tmp/segart/segment_issue_docling.py
TOCS=/Users/brewster/tmp/segart/tmp/tocs

# 4 ABBYY periodicals at similar sizes to our SIM corpus, from different
# publishers / eras to get a representative spread.
abbyy_items=(
  CityOfRutlandAnnualReport1942
  britishaffairs0041unse
  economicbulletin05n2unse
  EtudeAugust1936
)

for item in "${abbyy_items[@]}"; do
  echo "===== $item ====="
  if [[ ! -f "$ITEMS/$item/${item}.pdf" ]]; then
    echo "downloading PDF..."
    ia download "$item" --glob "*.pdf" --destdir "$ITEMS" 2>&1 | tail -2
  fi
  if [[ ! -f "$ITEMS/$item/${item}_page_numbers.json" ]]; then
    echo "downloading page_numbers..."
    ia download "$item" --glob "*_page_numbers.json" --destdir "$ITEMS" 2>&1 | tail -2 || echo "  (no page_numbers.json — skipping)"
  fi
  if [[ -f "$ITEMS/$item/${item}_page_numbers.json" ]]; then
    t0=$(date +%s)
    "$SCRIPT" "$item" --cache-dir "$ITEMS" -o "$TOCS/${item}_toc.json" --device cpu 2>&1 | tail -5
    t1=$(date +%s)
    echo "took $((t1-t0))s"
  else
    echo "skipped: no page_numbers"
  fi
  echo
done
