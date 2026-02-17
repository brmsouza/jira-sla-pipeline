from __future__ import annotations

import csv
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd

from src.common.logger import get_logger

logger = get_logger(__name__, layer="COMMON")


def new_run_id() -> str:
    # ISO compacto p/ facilitar ordenação
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dirs() -> None:
    (Path("data") / "audit").mkdir(parents=True, exist_ok=True)


def count_csv_rows(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        # não conta header
        with path.open("r", encoding="utf-8", newline="") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except Exception:
        return 0


def append_run_log(row: Dict[str, Any]) -> None:
    """
    data/audit/run_log.csv
    """
    ensure_dirs()
    out = Path("data") / "audit" / "run_log.csv"
    file_exists = out.exists() and out.stat().st_size > 0

    fieldnames = [
        "run_id",
        "step",
        "status",
        "start_ts",
        "end_ts",
        "duration_s",
        "rows_out",
        "dq_total",
        "dq_valid",
        "dq_missing",
        "dq_invalid",
        "artifact",
        "error",
    ]

    with out.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        # garante chaves
        safe = {k: row.get(k, "") for k in fieldnames}
        w.writerow(safe)


def write_lineage(run_id: str, mapping: Dict[str, Any]) -> Path:
    """
    data/audit/lineage.json
    """
    ensure_dirs()
    out = Path("data") / "audit" / "lineage.json"

    payload = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lineage": mapping,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def mask_name(value: str) -> str:
    """
    Minimal LGPD-compliant pseudonymization.
    
    Displays only the first 3 characters of the first name
    plus a short hash suffix to preserve uniqueness.

    Examples:
      "Maria Oliveira" -> "Mar*** (a1b2c3d4)"
      "Jo"             -> "Jo*** (a1b2c3d4)"
      "A"              -> "A*** (a1b2c3d4)"
    """
    if not value or value == "N/A":
        return value

    cleaned = str(value).strip()
    if not cleaned:
        return value

    # Short hash to preserve deterministic uniqueness
    h = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:8]

    # Extract first name and keep up to 3 characters
    first = cleaned.split(" ")[0]
    prefix = first[:3] if len(first) >= 3 else first

    return f"{prefix}*** ({h})"




