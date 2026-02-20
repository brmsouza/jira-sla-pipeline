from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Accept both lower-case and upper-case inputs by normalizing internally.
Layer = Literal["pipeline", "bronze", "silver", "gold", "dashboard"]


@dataclass(frozen=True)
class LogFiles:
    info_log: Path
    error_log: Path


class _LayerFilter(logging.Filter):
    def __init__(self, layer_label: str) -> None:
        super().__init__()
        self.layer_label = layer_label

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "layer"):
            record.layer = self.layer_label
        return True


class _MaxLevelFilter(logging.Filter):
    """Allow records up to a maximum level (inclusive)."""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _normalize_layer(layer: str) -> str:
    """
    Normalizes a layer string to a canonical format.

    Examples:
      "BRONZE" -> "bronze"
      "Silver" -> "silver"
      "PIPELINE" -> "pipeline"
    """
    return layer.strip().lower()


def _layer_emoji(layer: str) -> str:
    layer = _normalize_layer(layer)
    mapping = {
        "pipeline": "🧩",
        "bronze": "🥉",
        "silver": "🥈",
        "gold": "🥇",
        "dashboard": "📊",
    }
    # safer: fallback to pipeline icon for unknown layers
    return mapping.get(layer, "🧩")


def _build_log_files(layer: str) -> LogFiles:
    layer = _normalize_layer(layer)
    base = Path("logs") / layer
    _ensure_dir(base)

    return LogFiles(
        info_log=base / f"{layer}.log",
        error_log=base / f"{layer}_errors.log",
    )


def _env_truthy(name: str, default: str = "0") -> bool:
    """
    Interprets an env var as boolean.
    Truthy values: 1, true, yes, y, on (case-insensitive)
    """
    raw = os.getenv(name, default).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def get_logger(name: str, layer: str = "pipeline") -> logging.Logger:
    """
    Returns a configured logger.

    Default behavior (professional / CI-friendly):
      - NO console output (silent terminal)
      - logs/<layer>/<layer>.log (INFO..WARNING)
      - logs/<layer>/<layer>_errors.log (ERROR+)

    Optional:
      - Enable console output by setting: LOG_TO_CONSOLE=1
    """
    layer_norm = _normalize_layer(layer)

    logger_name = f"{layer_norm}.{name}"
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        return logger

    # Capture everything; handlers decide what to write.
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    logs = _build_log_files(layer_norm)
    layer_label = f"{_layer_emoji(layer_norm)} {layer_norm.upper()}"

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(layer)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Optional console handler (disabled by default)
    if _env_truthy("LOG_TO_CONSOLE", default="0"):
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        sh.addFilter(_LayerFilter(layer_label))
        logger.addHandler(sh)

    # File handler: info (up to WARNING)
    # delay=True helps on Windows by opening the file only when the first log record is emitted.
    fh_info = logging.FileHandler(logs.info_log, encoding="utf-8", delay=True)
    fh_info.setLevel(logging.INFO)
    fh_info.setFormatter(fmt)
    fh_info.addFilter(_LayerFilter(layer_label))
    fh_info.addFilter(_MaxLevelFilter(logging.WARNING))
    logger.addHandler(fh_info)

    # File handler: errors (ERROR+)
    fh_err = logging.FileHandler(logs.error_log, encoding="utf-8", delay=True)
    fh_err.setLevel(logging.ERROR)
    fh_err.setFormatter(fmt)
    fh_err.addFilter(_LayerFilter(layer_label))
    logger.addHandler(fh_err)

    return logger