from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectPaths:
    """
    Centralized project paths (relative to repository root).
    Keep it explicit and readable for reviewers.
    """
    root:        str = "."
    resources:   str = "resources"
    pipeline:    str = "pipeline"

    data_source: str = "data/source"   # local landing zone (Option B)
    data_bronze: str = "data/bronze"
    data_silver: str = "data/silver"
    data_gold:   str = "data/gold"

    logs:        str = "logs"
