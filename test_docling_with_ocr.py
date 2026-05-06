#!/usr/bin/env python3
"""
Test: re-run docling on a known item with do_ocr=True and
do_table_structure=True, comparing the new output against the existing
CPU-mode cache (which has both off).

Saves to <item>_docling_ocrtbl.json.gz so the existing v0.13 cache
isn't overwritten.

Usage:
    ./test_docling_with_ocr.py <item> [--page-range start end]
"""
import argparse
import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CACHE_ROOT = Path.home() / "tmp" / "segart" / "tmp" / "items"


def fetch_pdf(item):
    item_dir = CACHE_ROOT / item
    item_dir.mkdir(parents=True, exist_ok=True)
    pdf = item_dir / f"{item}.pdf"
    if pdf.exists():
        return pdf
    print(f"downloading PDF for {item}...", file=sys.stderr)
    subprocess.run(
        ["ia", "download", item, "--glob", "*.pdf",
         "--destdir", str(CACHE_ROOT)],
        check=True,
    )
    if not pdf.exists():
        # ia sometimes places files differently; find it
        cand = list(item_dir.glob(f"{item}*.pdf"))
        if cand:
            return cand[0]
        raise FileNotFoundError(f"no PDF found for {item}")
    return pdf


def docling_convert_full(pdf_path, do_ocr=True, do_tables=True, device="cpu"):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice

    dev = AcceleratorDevice.CPU if device == "cpu" else AcceleratorDevice.MPS
    opts = PdfPipelineOptions()
    opts.do_ocr = do_ocr
    opts.do_table_structure = do_tables
    opts.accelerator_options = AcceleratorOptions(device=dev, num_threads=4)
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return conv.convert(str(pdf_path)).document


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("item", help="IA item identifier")
    p.add_argument("--device", default="cpu", choices=["cpu", "mps"])
    p.add_argument("--no-ocr", action="store_true")
    p.add_argument("--no-tables", action="store_true")
    args = p.parse_args()

    pdf = fetch_pdf(args.item)
    print(f"PDF: {pdf} ({pdf.stat().st_size // 1024 // 1024} MB)",
          file=sys.stderr)

    t0 = time.time()
    doc = docling_convert_full(
        pdf,
        do_ocr=not args.no_ocr,
        do_tables=not args.no_tables,
        device=args.device,
    )
    elapsed = time.time() - t0
    print(f"docling conversion took {elapsed:.1f}s "
          f"(do_ocr={not args.no_ocr}, do_tables={not args.no_tables}, "
          f"device={args.device})",
          file=sys.stderr)

    out = CACHE_ROOT / args.item / f"{args.item}_docling_ocrtbl.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        fh.write(doc.model_dump_json())
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
