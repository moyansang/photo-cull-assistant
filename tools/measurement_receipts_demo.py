#!/usr/bin/env python3
"""Explicit synthetic pixels -> real scorer/capture -> frozen export -> offline report.

Requires a clean checkout and locally installed production image dependencies.
Never downloads models or reads a user's photographs. Outputs are new-only.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image
from ai_cull_assistant.measurement_receipts import MeasurementRecorder
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import screen_assets
from ai_cull_assistant.workflow import ScanResult
import frozen_prediction_export as exporter
import offline_evaluation as evaluation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory outside checkout")
    args = parser.parse_args(argv)
    output = evaluation.outside_repository(args.output)
    if output.exists():
        parser.error("output already exists; choose a new directory")
    recorder = MeasurementRecorder(synthetic=True)
    if not recorder.identity.get("verified"):
        parser.error("clean verified Git checkout required; commit/review changes first, never fabricate code identity")
    output.mkdir(parents=True, exist_ok=False)
    images = output / "synthetic-input"
    images.mkdir()
    path = images / "checker.png"
    image = Image.new("RGB", (640, 640))
    image.putdata([(255, 255, 255) if (x // 5 + y // 5) % 2 else (0, 0, 0)
                   for y in range(640) for x in range(640)])
    image.save(path)
    image.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = PhotoAsset(path.stem, path, path, None, path, datetime(2026, 1, 1), ".png",
                       group_id=7, preview_path=path)
    # This is the same real synchronous scoring entrypoint used by production;
    # the only addition is the explicitly supplied observer.
    screen_assets([asset], analysis_mode="landscape", evaluation_recorder=recorder)
    workspace = output / "synthetic-workspace"
    workspace.mkdir()
    result = ScanResult([asset], workspace / "previews", workspace / "contacts", workspace,
                        workspace / "groups.json", input_dir=images)
    samples = evaluation.synthetic_documents(evaluation.make_catalog(ROOT, recorder.identity["source_git"]))["samples"]
    samples["rows"] = samples["rows"][:1]
    sample = samples["rows"][0]
    sample.update(scene_type="landscape", exception_tags=[],
                  source_authorization="generated checker pixels; no human subject or private photograph",
                  evaluation_source_id="generated-png",
                  sources=[{"source_id": "generated-png", "format": "PNG", "sha256": before}])
    bindings = {sample["asset_id"]: {"primary_path": str(path), "sources": {"generated-png": str(path)}}}
    frozen = recorder.freeze(result, samples, bindings, output / "frozen")
    status = exporter.main(["--session", str(frozen / "scan-session.json"),
                            "--samples", str(frozen / "samples.json"),
                            "--provenance", str(frozen / "measurement-receipt.json"),
                            "--output", str(output / "export")])
    if status:
        return status
    predictions = evaluation.read_json(output / "export" / "predictions.json")
    if predictions["rows"][0]["status"] != "ok":
        print("Capture was conservatively rejected; see export/diagnostics.json", file=sys.stderr)
        return 2
    status = evaluation.main(["evaluate", "--samples", str(frozen / "samples.json"),
                              "--predictions", str(output / "export" / "predictions.json"),
                              "--annotations", str(output / "export" / "annotations.json"),
                              "--catalog", str(output / "export" / "catalog.json"),
                              "--output", str(output / "evaluation")])
    if before != hashlib.sha256(path.read_bytes()).hexdigest():
        raise RuntimeError("synthetic input unexpectedly changed")
    evaluation.write_new(output / "demo-result.json", {
        "kind": "synthetic_actual_measurement_demo", "source_git": recorder.identity["source_git"],
        "run_id": recorder.run_id, "input_unchanged": True, "group_id": asset.group_id,
        "recommendation": asset.local_recommendation, "cost": recorder.cost,
        "real_world_accuracy": {"value": None, "status": "尚未测量"},
        "limitations": ["generated PNG only", "no RAW decoder or portrait accuracy acceptance",
                        "blank human annotations; no measured accuracy", "no GUI ProcessingJob capture"]})
    print(json.dumps({"output": str(output), "cost": recorder.cost, "real_world_accuracy": "尚未测量"}, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
