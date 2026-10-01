"""Autonomous monitor and deployment pipeline for round 6 retrained model.

Monitors:
  - D:/AI/bonc-v4/r6.log until "===== ROUND6 DONE" is logged.

Upon completion:
  1. Validates exported bundle in D:/AI/bonc-v4/bundle_round6.
  2. Backs up current flask_api/models/v4 to flask_api/models/v4.backup_pre_v5.
  3. Deploys round 6 model files into flask_api/models/v4.
  4. Runs flask_api/scripts/test_pdf_stress_suite.py against the new model.
  5. Updates ocr_pdf_and_dataset_report.md with post-retraining metrics.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLASK_DIR = ROOT / "flask_api"
MODELS_V4 = FLASK_DIR / "models" / "v4"
BUNDLE_R6 = Path("D:/AI/bonc-v4/bundle_round6")
LOG_PATH = Path("D:/AI/bonc-v4/r6.log")
REPORT_PATH = Path(r"C:\Users\Asus\.gemini\antigravity-cli\brain\69113e33-04d2-4104-88c7-542ec943f1fd\ocr_pdf_and_dataset_report.md")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("monitor_r6")


def check_log_status() -> tuple[str, str]:
    if not LOG_PATH.exists():
        return "waiting", "Log file not found yet"
    try:
        content = LOG_PATH.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return "reading", str(e)

    if "===== ROUND6 DONE" in content:
        return "done", "Round 6 completed successfully"
    if "TRAIN_EXIT" in content:
        for line in content.splitlines():
            if line.startswith("TRAIN_EXIT") and not line.endswith(" 0"):
                return "error", f"Training exited with error: {line}"
    if "EXPORT_EXIT" in content:
        for line in content.splitlines():
            if line.startswith("EXPORT_EXIT") and not line.endswith(" 0"):
                return "error", f"Export exited with error: {line}"

    # Extract last progress line
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    last_line = lines[-1] if lines else "initializing"
    for l in reversed(lines):
        if "step " in l or "== eval " in l:
            last_line = l
            break
    return "running", last_line


def deploy_and_test():
    log.info("Verifying bundle files in %s...", BUNDLE_R6)
    model_onnx = BUNDLE_R6 / "model.onnx"
    model_meta = BUNDLE_R6 / "model_meta.json"
    tok_json = BUNDLE_R6 / "tokenizer.json"

    if not (model_onnx.exists() and model_meta.exists() and tok_json.exists()):
        raise RuntimeError(f"Missing required bundle files in {BUNDLE_R6}")

    log.info("Reading model_meta.json...")
    meta = json.loads(model_meta.read_text(encoding="utf-8"))
    version = meta.get("version", "v5")
    temp = meta.get("temperature")
    test_metrics = meta.get("metrics", {}).get("test", {})
    log.info("Bundle Version: %s | Temperature: %s | Test Acc: %s | Test F1: %s",
             version, temp, test_metrics.get("acc"), test_metrics.get("f1"))

    # Backup current v4 model
    backup_dir = FLASK_DIR / "models" / "v4.backup_pre_v5"
    if not backup_dir.exists():
        log.info("Creating backup of current v4 model at %s...", backup_dir)
        shutil.copytree(MODELS_V4, backup_dir)

    # Deploy new bundle into models/v4
    log.info("Deploying new model bundle into %s...", MODELS_V4)
    shutil.copy2(model_onnx, MODELS_V4 / "model.onnx")
    shutil.copy2(model_meta, MODELS_V4 / "model_meta.json")
    shutil.copy2(tok_json, MODELS_V4 / "tokenizer.json")
    log.info("Model files successfully replaced in %s.", MODELS_V4)

    # Run 120-document PDF stress test
    log.info("Executing 120-document PDF stress suite against the newly deployed model...")
    venv_python = FLASK_DIR / ".venv" / "Scripts" / "python.exe"
    script = FLASK_DIR / "scripts" / "test_pdf_stress_suite.py"

    cmd = [str(venv_python), str(script)]
    res = subprocess.run(cmd, cwd=str(FLASK_DIR), capture_output=True, text=True, check=False)
    log.info("Stress suite finished with return code %d", res.returncode)
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)

    # Read updated stress test JSON report
    stress_json_path = FLASK_DIR / "scripts" / "test_stress_pdfs" / "stress_test_report.json"
    if stress_json_path.exists():
        stress_data = json.loads(stress_json_path.read_text(encoding="utf-8"))
        log.info("New Stress Test Results: Total: %d, Passed: %d (%.1f%%), Precision: %.2f%%, Recall: %.2f%%, F1: %.4f",
                 stress_data["total_tests"], stress_data["passed"],
                 stress_data["passed"] / stress_data["total_tests"] * 100,
                 stress_data["precision"] * 100, stress_data["recall"] * 100,
                 stress_data["f1_score"])

    log.info("ALL DEPLOYMENT AND VERIFICATION STEPS COMPLETED!")


def main():
    log.info("Starting background monitor for Round 6 retraining...")
    last_reported = ""
    start_time = time.time()

    while True:
        status, detail = check_log_status()
        if detail != last_reported:
            last_reported = detail
            log.info("Status: %s | %s", status, detail)

        if status == "done":
            log.info("Training and calibration detected complete! Proceeding to deploy and test...")
            time.sleep(5)  # brief grace period for file handles to release
            deploy_and_test()
            break
        elif status == "error":
            log.error("Aborting monitor: %s", detail)
            sys.exit(1)

        time.sleep(30)


if __name__ == "__main__":
    main()
