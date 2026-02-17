from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from src.common.logger import get_logger
from src.common.governance import append_run_log, count_csv_rows

logger = get_logger(__name__, layer="PIPELINE")


# ----------------------------
# Lineage (audit)
# ----------------------------
LINEAGE_PATH = Path("data/audit/lineage.json")


def append_lineage(record: dict[str, Any]) -> None:
    """
    Appends a lineage record into data/audit/lineage.json (list of records).
    Creates folders/file if missing.
    """
    LINEAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if LINEAGE_PATH.exists():
        try:
            content = LINEAGE_PATH.read_text(encoding="utf-8").strip()
            data = json.loads(content) if content else []
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []
    else:
        data = []

    data.append(record)
    LINEAGE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class StepResult:
    name: str
    ok: bool
    output: Any = None
    error: Optional[Exception] = None
    started_at: str = ""
    ended_at: str = ""
    duration_s: float = 0.0


def run_step(step_name: str, fn: Callable[[], Any], *, run_id: str, artifact_path: str = "") -> StepResult:
    t0 = time.time()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    logger.info(f"▶ STEP | {step_name}")

    try:
        out = fn()
        ok = True
        err = None
        logger.info(f"✅ STEP | {step_name} | done")
    except Exception as e:
        ok = False
        out = None
        err = e
        logger.error(f"❌ STEP | {step_name} | failed", exc_info=True)

    ended = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dur = round(time.time() - t0, 2)

    # rows_out: se artifact_path for CSV, conta linhas
    rows_out = ""
    ap = Path(artifact_path) if artifact_path else None
    if ap and ap.suffix.lower() == ".csv":
        rows_out = str(count_csv_rows(ap))

    status = "OK" if ok else "FAILED"

    # ----------------------------
    # Run log (já existe)
    # ----------------------------
    append_run_log(
        {
            "run_id": run_id,
            "step": step_name,
            "status": status,
            "start_ts": started,
            "end_ts": ended,
            "duration_s": dur,
            "rows_out": rows_out,
            "dq_total": "",
            "dq_valid": "",
            "dq_missing": "",
            "dq_invalid": "",
            "artifact": artifact_path,
            "error": "" if ok else repr(err),
        }
    )

    # ----------------------------
    # Lineage (novo) -> data/audit/lineage.json
    # ----------------------------
    append_lineage(
        {
            "run_id": run_id,
            "step": step_name,
            "status": status,
            "start_ts": started,
            "end_ts": ended,
            "duration_s": dur,
            "artifact": artifact_path,
            "rows_out": rows_out,
            "error": "" if ok else repr(err),
        }
    )

    return StepResult(
        name=step_name,
        ok=ok,
        output=out,
        error=err,
        started_at=started,
        ended_at=ended,
        duration_s=dur,
    )
