r"""Test the 14 real BONC PDFs from C:\Users\Asus\Downloads\pdf_data against the live system.

Observes:
  - Extraction mechanism (native text vs RapidOCR)
  - Character count and page breakdown
  - Layer 1 Gate triggers (if any)
  - Layer 2 ONNX Model risk scores and predicted label
  - Final decision (allow / reject / revise)
  - Latency and processing time
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RESULT_SINK"] = "log"

from app import create_app
from app.config import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_bonc_pdfs")

PDF_DIR = Path(r"C:\Users\Asus\Downloads\pdf_data")


def main():
    settings = load_settings()
    app = create_app(settings)
    client = app.test_client()

    files = sorted(PDF_DIR.glob("*.pdf"))
    print(f"\n================================================================================")
    print(f"TESTING {len(files)} REAL BONC PDFs FROM {PDF_DIR}")
    print(f"Model: {settings.model_dir.name} | PDF Max Bytes: {settings.pdf.max_bytes / (1024*1024):.1f} MB")
    print(f"================================================================================\n")

    results = []

    for idx, fpath in enumerate(files, 1):
        size_bytes = fpath.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        print(f"\n[{idx:02d}/{len(files):02d}] Testing: {fpath.name} ({size_mb:.2f} MB)...")

        t0 = time.perf_counter()
        with open(fpath, "rb") as f:
            pdf_bytes = f.read()

        resp = client.post(
            "/v1/moderate/pdf",
            data={
                "file": (io.BytesIO(pdf_bytes), fpath.name, "application/pdf"),
                "content_type": "article"
            },
            content_type="multipart/form-data"
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        status_code = resp.status_code
        data = resp.get_json() or {}

        res = {
            "index": idx,
            "filename": fpath.name,
            "size_mb": round(size_mb, 2),
            "size_bytes": size_bytes,
            "status_code": status_code,
            "elapsed_ms": round(elapsed_ms, 1),
            "response": data
        }
        results.append(res)

        if status_code == 200:
            decision = data.get("decision")
            decided_by = data.get("decided_by")
            risk = data.get("risk_score")
            label = data.get("predicted_label")
            pdf_meta = data.get("pdf", {})
            chars = pdf_meta.get("extracted_chars", 0)
            pages = pdf_meta.get("page_count", 0)
            ocr_pages = pdf_meta.get("ocr_pages", [])
            unreadable = pdf_meta.get("unreadable_pages", [])
            gate_matches = data.get("gate_matches", [])

            print(f"    Status: 200 OK | Decision: {decision.upper()} (by {decided_by}) | Risk: {risk:.4f} ({label})")
            print(f"    Pages: {pages} | Chars: {chars:,} | OCR Pages: {ocr_pages} | Unreadable: {unreadable}")
            if gate_matches:
                print(f"    Gate Matches: {gate_matches}")
        else:
            code = data.get("code") or data.get("error")
            msg = data.get("message")
            extra = {k: v for k, v in data.items() if k not in ("code", "message", "status")}
            print(f"    Status: {status_code} {code} | Message: {msg}")
            if extra:
                print(f"    Extra details: {extra}")

    out_file = ROOT / "scripts" / "bonc_downloaded_pdfs_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved full results to {out_file}")


if __name__ == "__main__":
    main()
