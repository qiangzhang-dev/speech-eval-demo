"""Versioned project configuration loaders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_threshold_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the project threshold file and derive the runtime CER bands."""

    candidate = Path(path) if path is not None else Path("config/thresholds.json")
    if not candidate.exists():
        return {
            "threshold_version": "runtime-default-v1",
            "approval_status": "missing",
            "effective": False,
            "pass_max": 0.10,
            "attention_max": 0.20,
            "source": str(candidate),
        }
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    metric = payload.get("metrics", {}).get("CER", {})
    bands = metric.get("bands", [])
    pass_max = 0.10
    attention_max = 0.20
    for band in bands:
        if band.get("status") == "通过" and band.get("upper") is not None:
            pass_max = float(band["upper"])
        elif band.get("status") == "需关注" and band.get("upper") is not None:
            attention_max = float(band["upper"])
    return {
        "threshold_version": str(payload.get("threshold_version", "unknown")),
        "approval_status": str(payload.get("approval_status", "unknown")),
        "effective": bool(payload.get("effective", False)),
        "pass_max": pass_max,
        "attention_max": attention_max,
        "source": str(candidate),
    }

