"""
Quality Gate - End-to-end validation for the Jira SLA pipeline outputs.

Purpose
-------
This script validates that the pipeline produced the required Gold outputs
and that they conform to basic expectations (non-empty, required columns, etc.).

It is intended to be run after `python main.py` and fail fast with clear errors.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
GOLD_DIR = DATA_DIR / "gold"


@dataclass(frozen=True)
class DatasetSpec:
    """Dataset requirements used by the Quality Gate."""

    path: Path
    required_columns: set[str]
    must_have_rows: bool = True


def _fail(msg: str) -> None:
    """Exit the program with a clear error message."""
    print(f"\n❌ QUALITY GATE FAILED: {msg}\n")
    raise SystemExit(1)


def _warn(msg: str) -> None:
    """Print a non-fatal warning message."""
    print(f"⚠️  WARNING: {msg}")


def _ok(msg: str) -> None:
    """Print a success message."""
    print(f"✅ {msg}")


def _read_csv(path: Path) -> pd.DataFrame:
    """Read CSV using pandas with safe defaults."""
    try:
        return pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        _fail(f"Could not read CSV: {path} | error={exc}")
        raise


def _validate_file_exists(path: Path) -> None:
    """Ensure required file exists."""
    if not path.exists():
        _fail(f"Missing required file: {path}")


def _validate_columns(df: pd.DataFrame, required: set[str], path: Path) -> None:
    """Ensure required columns exist."""
    missing = sorted(required - set(df.columns))
    if missing:
        _fail(f"{path.name} missing required columns: {missing}")


def _validate_non_empty(df: pd.DataFrame, path: Path) -> None:
    """Ensure dataset has at least one row."""
    if df.empty:
        _fail(f"{path.name} is empty (0 rows)")


def _validate_no_duplicate_keys(df: pd.DataFrame, key_col: str, path: Path) -> None:
    """Warn (non-fatal) if there are duplicate keys."""
    if key_col not in df.columns:
        _warn(f"{path.name}: cannot validate duplicates because '{key_col}' is missing.")
        return
    dup_count = int(df[key_col].duplicated().sum())
    if dup_count > 0:
        _warn(f"{path.name}: found {dup_count} duplicated '{key_col}' values (non-fatal).")


def _validate_numeric_non_negative(
    df: pd.DataFrame,
    cols: Iterable[str],
    path: Path,
) -> None:
    """Warn (non-fatal) if numeric values are negative."""
    for c in cols:
        if c not in df.columns:
            _warn(f"{path.name}: cannot validate non-negative because '{c}' is missing.")
            continue
        ser = pd.to_numeric(df[c], errors="coerce")
        neg = int((ser < 0).sum())
        if neg > 0:
            _warn(f"{path.name}: column '{c}' has {neg} negative values (non-fatal).")


def build_specs() -> list[DatasetSpec]:
    """
    Define the datasets required by the challenge.

    Notes
    -----
    The required column names are aligned to the actual schema produced by the pipeline.
    """
    return [
        DatasetSpec(
            path=GOLD_DIR / "gold_sla_issues.csv",
            required_columns={
                "issue_id",
                "issue_key",
                "issue_type",
                "assignee_name",
                "priority",
                "status",
                "created_at",
                "resolved_at",
                "sla_expected_hours",
                "resolution_hours",
                "sla_met",
                "sla_status",
            },
            must_have_rows=True,
        ),
        DatasetSpec(
            path=GOLD_DIR / "gold_sla_backlog.csv",
            required_columns={
                "issue_id",
                "issue_key",
                "issue_type",
                "assignee_name",
                "priority",
                "status",
                "created_at",
                "as_of_ts",
                "sla_target_hours",
                "age_business_hours",
                "is_overdue",
            },
            must_have_rows=True,
        ),
        DatasetSpec(
            path=GOLD_DIR / "gold_sla_by_analyst.csv",
            required_columns={
                "assignee_name",
            },
            must_have_rows=True,
        ),
        DatasetSpec(
            path=GOLD_DIR / "gold_sla_by_issue_type.csv",
            required_columns={
                "issue_type",
            },
            must_have_rows=True,
        ),
    ]


def main() -> None:
    """Run the quality gate validations."""
    print("=== QUALITY GATE: Jira SLA Pipeline ===")
    print(f"Repo root: {REPO_ROOT}")
    print(f"Gold dir : {GOLD_DIR}")

    if not GOLD_DIR.exists():
        _fail(f"Gold directory not found: {GOLD_DIR}. Run `python main.py` first.")

    specs = build_specs()

    # 1) Existence + schema + non-empty checks
    for spec in specs:
        _validate_file_exists(spec.path)
        df = _read_csv(spec.path)
        _validate_columns(df, spec.required_columns, spec.path)

        if spec.must_have_rows:
            _validate_non_empty(df, spec.path)

        _ok(f"{spec.path.name}: OK | rows={len(df):,} | cols={len(df.columns):,}")

    # 2) Extra "board-friendly" validations (non-fatal)
    issues_path = GOLD_DIR / "gold_sla_issues.csv"
    issues = _read_csv(issues_path)
    _validate_no_duplicate_keys(issues, "issue_key", issues_path)

    _validate_numeric_non_negative(
        issues,
        cols=["sla_expected_hours", "resolution_hours"],
        path=issues_path,
    )

    backlog_path = GOLD_DIR / "gold_sla_backlog.csv"
    backlog = _read_csv(backlog_path)

    _validate_numeric_non_negative(
        backlog,
        cols=["sla_target_hours", "age_business_hours"],
        path=backlog_path,
    )

    # Optional (non-fatal): backlog should generally not have resolved_at filled
    if "resolved_at" in backlog.columns:
        resolved_non_null = int(pd.notna(backlog["resolved_at"]).sum())
        if resolved_non_null > 0:
            _warn(
                f"{backlog_path.name}: found {resolved_non_null} rows with resolved_at filled "
                "(backlog is expected to be mostly unresolved)."
            )

    print("\n✅ QUALITY GATE PASSED: All required outputs are present and valid.\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.\n")
        sys.exit(130)
