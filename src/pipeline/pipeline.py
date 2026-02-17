from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from src.common.logger import get_logger
from src.pipeline.runner import run_step

# Cleanup
from src.common.cleanup_environment import cleanup

# Bronze
from src.bronze.extract_issues import extract_issues_to_bronze
from src.bronze.extract_holidays import extract_holidays_to_bronze

# Silver
from src.silver.normalize_issues import normalize_issues_to_silver
from src.silver.build_calendar import build_calendar_to_silver

# Gold
from src.gold.build_gold import build_gold_resolved, build_gold_backlog


logger = get_logger(__name__, layer="PIPELINE")


def _as_path(x: Any, fallback: Path) -> Path:
    """Best-effort cast output to Path, with fallback."""
    if isinstance(x, Path):
        return x
    if isinstance(x, str) and x.strip():
        return Path(x)
    return fallback


def _pick_path_from_output(output: Any, index: int, fallback: Path) -> Path:
    """
    Some steps return a single Path/string.
    Others (like normalize_issues_to_silver) may return a tuple:
        (valid_path, invalid_path, dq_path)
    This helper extracts the desired item safely.
    """
    if isinstance(output, (list, tuple)) and len(output) > index:
        return _as_path(output[index], fallback)
    return _as_path(output, fallback)


def run_pipeline() -> None:
    logger.info("PIPELINE | Starting...")

    run_id = str(uuid.uuid4())
    base_dir = Path(__file__).resolve().parents[2]

    # ---- canonical fallback paths (used if step doesn't return a path)
    bronze_issues_fallback = base_dir / "data" / "bronze" / "jira_issues_raw.json"
    bronze_holidays_fallback = base_dir / "data" / "bronze" / "bronze_holidays.csv"

    silver_valid_fallback = base_dir / "data" / "silver" / "silver_issues.csv"
    silver_invalid_fallback = base_dir / "data" / "silver" / "silver_issues_invalid.csv"
    silver_calendar_fallback = base_dir / "data" / "silver" / "silver_calendar.csv"

    # =========================
    # CLEANUP (before everything)
    # =========================
    r0 = run_step(
        "common.cleanup_environment",
        lambda: cleanup(),
        run_id=run_id,
    )
    if r0.error:
        raise r0.error

    # =========================
    # BRONZE
    # =========================
    r1 = run_step(
        "bronze.extract_issues",
        lambda: extract_issues_to_bronze(),
        run_id=run_id,
    )
    if r1.error:
        raise r1.error
    bronze_issues_path = _as_path(r1.output, bronze_issues_fallback)

    r2 = run_step(
        "bronze.extract_holidays",
        lambda: extract_holidays_to_bronze(),
        run_id=run_id,
    )
    if r2.error:
        raise r2.error
    # extract_holidays_to_bronze retorna DF, então aqui usamos o caminho padrão do bronze
    bronze_holidays_path = bronze_holidays_fallback

    # =========================
    # SILVER
    # =========================
    r3 = run_step(
        "silver.normalize_issues",
        lambda: normalize_issues_to_silver(bronze_issues_path),
        run_id=run_id,
    )
    if r3.error:
        raise r3.error

    # normalize_issues_to_silver pode retornar:
    #   - Path do valid
    #   - ou (valid_path, invalid_path, dq_path)
    silver_valid_issues_path = _pick_path_from_output(
        r3.output, 0, silver_valid_fallback
    )
    silver_invalid_issues_path = _pick_path_from_output(
        r3.output, 1, silver_invalid_fallback
    )

    r4 = run_step(
        "silver.build_calendar",
        lambda: build_calendar_to_silver(
            silver_valid_issues_path=silver_valid_issues_path,
            bronze_holidays_path=bronze_holidays_path,
        ),
        run_id=run_id,
    )
    if r4.error:
        raise r4.error
    silver_calendar_path = _as_path(r4.output, silver_calendar_fallback)

    # =========================
    # GOLD
    # =========================
    r5 = run_step(
        "gold.build_resolved",
        lambda: build_gold_resolved(
            silver_valid_issues_path=silver_valid_issues_path,
            silver_calendar_path=silver_calendar_path,
            holidays_path=bronze_holidays_path,
        ),
        run_id=run_id,
    )
    if r5.error:
        raise r5.error

    r6 = run_step(
        "gold.build_backlog",
        lambda: build_gold_backlog(
            silver_invalid_issues_path=silver_invalid_issues_path,
            silver_calendar_path=silver_calendar_path,
            holidays_path=bronze_holidays_path,
        ),
        run_id=run_id,
    )
    if r6.error:
        raise r6.error

    logger.info("PIPELINE | Finished successfully ✅")
