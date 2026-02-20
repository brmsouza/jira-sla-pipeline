from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

from src.common.logger import get_logger


@dataclass(frozen=True)
class CleanupResult:
    removed: int
    skipped_locked: int
    missing: int
    errors: int


def _is_winerror_32(exc: BaseException) -> bool:
    """
    Windows-specific: file is being used by another process.
    """
    return getattr(exc, "winerror", None) == 32


def _safe_unlink(path: Path, logger: logging.Logger) -> Tuple[str, str]:
    """
    Attempts to remove a file safely.

    Returns:
        (status, message)
        status ∈ {"removed", "skipped_locked", "missing", "error"}
    """
    try:
        if not path.exists():
            return ("missing", f"Missing: {path.as_posix()}")

        path.unlink()
        return ("removed", f"Removed: {path.as_posix()}")

    except PermissionError as e:
        if _is_winerror_32(e):
            return ("skipped_locked", f"Skipped locked (WinError 32): {path.as_posix()}")
        logger.error("Permission error removing %s", path.as_posix(), exc_info=True)
        return ("error", f"PermissionError: {path.as_posix()}")

    except OSError as e:
        if _is_winerror_32(e):
            return ("skipped_locked", f"Skipped locked (WinError 32): {path.as_posix()}")
        logger.error("OS error removing %s", path.as_posix(), exc_info=True)
        return ("error", f"OSError: {path.as_posix()}")

    except Exception:
        logger.error("Unexpected error removing %s", path.as_posix(), exc_info=True)
        return ("error", f"Unexpected: {path.as_posix()}")


def _build_cleanup_targets() -> List[Path]:
    """
    Defines which files should be cleaned between runs.
    Adjust if necessary.
    """
    targets: List[Path] = [
        # Data outputs
        Path("data/audit/run_log.csv"),
        Path("data/bronze/bronze_holidays.csv"),
        Path("data/silver/silver_calendar.csv"),
        Path("data/silver/silver_issues.csv"),
        Path("data/silver/silver_issues_invalid.csv"),
        Path("data/gold/gold_sla_issues.csv"),
        Path("data/gold/gold_sla_backlog.csv"),
        Path("data/gold/gold_sla_by_analyst.csv"),
        Path("data/gold/gold_sla_by_issue_type.csv"),
        # Logs
        Path("logs/bronze/bronze.log"),
        Path("logs/bronze/bronze_errors.log"),
        Path("logs/common/common.log"),
        Path("logs/common/common_errors.log"),
        Path("logs/silver/silver.log"),
        Path("logs/silver/silver_errors.log"),
        Path("logs/gold/gold.log"),
        Path("logs/gold/gold_errors.log"),
        Path("logs/pipeline/pipeline.log"),
        Path("logs/pipeline/pipeline_errors.log"),
        Path("logs/dashboard/dashboard.log"),
        Path("logs/dashboard/dashboard_errors.log"),
    ]

    return targets


def cleanup_environment(
    extra_paths: Iterable[str | Path] | None = None,
) -> CleanupResult:
    """
    Cleans previous run artifacts.

    - Does NOT print anything to terminal.
    - Ignores WinError 32 silently.
    - Logs summary only in file logs.
    """

    # Close logging handlers before deleting log files
    logging.shutdown()

    logger = get_logger(__name__, layer="pipeline")

    targets = _build_cleanup_targets()

    if extra_paths:
        targets.extend(Path(p) for p in extra_paths)

    removed = 0
    skipped_locked = 0
    missing = 0
    errors = 0

    logger.info("Cleanup started | targets=%s", len(targets))

    for path in targets:
        status, msg = _safe_unlink(path, logger)

        if status == "removed":
            removed += 1
            logger.debug(msg)

        elif status == "skipped_locked":
            skipped_locked += 1
            logger.debug(msg)

        elif status == "missing":
            missing += 1
            logger.debug(msg)

        else:
            errors += 1
            logger.debug(msg)

    logger.info(
        "Cleanup completed | removed=%s | skipped_locked=%s | missing=%s | errors=%s",
        removed,
        skipped_locked,
        missing,
        errors,
    )

    return CleanupResult(
        removed=removed,
        skipped_locked=skipped_locked,
        missing=missing,
        errors=errors,
    )


# ✅ Compatibility with existing import:
# from src.common.cleanup_environment import cleanup
def cleanup() -> CleanupResult:
    return cleanup_environment()


if __name__ == "__main__":
    cleanup_environment()