"""
src/gold/build_gold.py

Gold Layer builder.

This module produces:
- data/gold/gold_sla_issues.csv            (resolved issues only: Done/Resolved)
- data/gold/gold_sla_by_analyst.csv        (aggregations)
- data/gold/gold_sla_by_issue_type.csv     (aggregations)
- data/gold/gold_sla_backlog.csv           (backlog: missing resolved_at)

Key rules (per your challenge doc):
- Business days only (Mon–Fri)
- Exclude national holidays (from public API -> bronze_holidays.csv)
- No working-hours window (no 9–18); count 24h over business days
- SLA applies only to Done/Resolved issues (for resolved dataset)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.common.logger import get_logger
from src.gold.sla_calculation import (
    calculate_business_hours,
    normalize_holidays,
    sla_expected_hours,
    sla_met,
)

logger = get_logger(__name__, layer="GOLD")

BASE_DIR = Path(__file__).resolve().parents[2]

# ==============================
# DEFAULT PATHS
# ==============================
DEFAULT_SILVER_VALID = BASE_DIR / "data" / "silver" / "silver_issues.csv"
DEFAULT_SILVER_INVALID = BASE_DIR / "data" / "silver" / "silver_issues_invalid.csv"
DEFAULT_SILVER_CALENDAR = BASE_DIR / "data" / "silver" / "silver_calendar.csv"
DEFAULT_BRONZE_HOLIDAYS = BASE_DIR / "data" / "bronze" / "bronze_holidays.csv"

DEFAULT_GOLD_RESOLVED = BASE_DIR / "data" / "gold" / "gold_sla_issues.csv"
DEFAULT_GOLD_BACKLOG = BASE_DIR / "data" / "gold" / "gold_sla_backlog.csv"
DEFAULT_GOLD_ANALYST = BASE_DIR / "data" / "gold" / "gold_sla_by_analyst.csv"
DEFAULT_GOLD_TYPE = BASE_DIR / "data" / "gold" / "gold_sla_by_issue_type.csv"


# ==============================
# HELPERS
# ==============================
def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_any(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)

    try:
        return pd.read_json(path)
    except Exception:
        return pd.read_json(path, lines=True)


def _holidays_from_file(path: Path) -> set[date]:
    """
    Reads holidays from:
      - bronze_holidays.csv (column 'date')
      - silver_calendar.csv (column 'date' where is_holiday == True)
      - other compatible formats (holiday_date/calendar_date)
    """
    df = _read_any(path)

    # If calendar table, filter only real holidays
    if "is_holiday" in df.columns:
        mask = df["is_holiday"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
        df = df[mask].copy()

    for col in ["date", "holiday_date", "calendar_date"]:
        if col in df.columns:
            return normalize_holidays(df[col].dropna().tolist())

    raise ValueError(f"No holiday date column found in {path}")


def _load_holidays(holidays_path: Path | None, calendar_path: Path | None) -> set[date]:
    holidays: set[date] = set()

    holidays_path = holidays_path or DEFAULT_BRONZE_HOLIDAYS
    calendar_path = calendar_path or DEFAULT_SILVER_CALENDAR

    if holidays_path.exists():
        holidays |= _holidays_from_file(holidays_path)
        logger.info(f"Holidays loaded from bronze: {len(holidays)}")

    if calendar_path.exists():
        holidays |= _holidays_from_file(calendar_path)
        logger.info(f"Holidays loaded from calendar: {len(holidays)}")

    return holidays


def _bool_series(s: pd.Series) -> pd.Series:
    """Robust conversion to boolean for columns that may be True/False or strings."""
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().map({"true": True, "false": False})


# =========================================================
# GOLD RESOLVED
# =========================================================
def build_gold_resolved(
    silver_valid_issues_path: Path | None = None,
    silver_calendar_path: Path | None = None,
    holidays_path: Path | None = None,
    gold_resolved_path: Path | None = None,
    gold_by_analyst_path: Path | None = None,
    gold_by_issue_type_path: Path | None = None,
) -> pd.DataFrame:
    """Build resolved SLA dataset and aggregations."""
    logger.info("Starting build_gold_resolved")

    silver_valid_issues_path = silver_valid_issues_path or DEFAULT_SILVER_VALID
    df = _read_any(silver_valid_issues_path)

    if df.empty:
        logger.warning("No valid issues found.")
        return df

    # ensure datetimes
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df["resolved_at"] = pd.to_datetime(df["resolved_at"], errors="coerce", utc=True)

    # SLA only for Done/Resolved
    df = df[df["status"].isin(["Done", "Resolved"])].copy()

    holidays = _load_holidays(holidays_path, silver_calendar_path)

    df["sla_expected_hours"] = df["priority"].apply(sla_expected_hours)

    df["resolution_hours"] = df.apply(
        lambda r: calculate_business_hours(r["created_at"], r["resolved_at"], holidays),
        axis=1,
    )

    df["sla_met"] = df.apply(
        lambda r: sla_met(r["resolution_hours"], r["sla_expected_hours"]),
        axis=1,
    )
    df["sla_status"] = df["sla_met"].apply(lambda x: "MET" if bool(x) else "BREACHED")

    # ==============================
    # SAVE OUTPUTS
    # ==============================
    gold_resolved_path = gold_resolved_path or DEFAULT_GOLD_RESOLVED
    gold_by_analyst_path = gold_by_analyst_path or DEFAULT_GOLD_ANALYST
    gold_by_issue_type_path = gold_by_issue_type_path or DEFAULT_GOLD_TYPE

    _ensure_parent(gold_resolved_path)
    df.to_csv(gold_resolved_path, index=False)
    logger.info(f"Gold RESOLVED saved: {gold_resolved_path} | rows={len(df)}")

    # Aggregations (include audit counts)
    sla_met_bool = _bool_series(df["sla_met"]).fillna(False)

    rep_analyst = (
        df.assign(_sla_met=sla_met_bool)
        .groupby("assignee_name", dropna=False)
        .agg(
            qtd=("issue_id", "count"),
            sla_avg=("resolution_hours", "mean"),
            sla_met_count=("_sla_met", lambda x: int((x == True).sum())),  # noqa: E712
            sla_breached_count=("_sla_met", lambda x: int((x == False).sum())),  # noqa: E712
        )
        .reset_index()
    )
    rep_analyst["sla_met_pct"] = (rep_analyst["sla_met_count"] / rep_analyst["qtd"]).round(4)
    rep_analyst.to_csv(gold_by_analyst_path, index=False)

    rep_type = (
        df.assign(_sla_met=sla_met_bool)
        .groupby("issue_type", dropna=False)
        .agg(
            qtd=("issue_id", "count"),
            sla_avg=("resolution_hours", "mean"),
            sla_met_count=("_sla_met", lambda x: int((x == True).sum())),  # noqa: E712
            sla_breached_count=("_sla_met", lambda x: int((x == False).sum())),  # noqa: E712
        )
        .reset_index()
    )
    rep_type["sla_met_pct"] = (rep_type["sla_met_count"] / rep_type["qtd"]).round(4)
    rep_type.to_csv(gold_by_issue_type_path, index=False)

    logger.info("Finished build_gold_resolved")
    return df


# =========================================================
# GOLD BACKLOG
# =========================================================
def build_gold_backlog(
    silver_invalid_issues_path: Path | None = None,
    silver_calendar_path: Path | None = None,
    holidays_path: Path | None = None,
    gold_backlog_path: Path | None = None,
) -> pd.DataFrame:
    """
    Build backlog dataset using Silver INVALID file.

    In your pipeline, backlog issues live in `silver_issues_invalid.csv` with:
      - row_status == 'MISSING' (resolved_at missing)

    We exclude:
      - INVALID rows (bad datetime / resolved_before_created)
    """
    logger.info("Starting build_gold_backlog")

    silver_invalid_issues_path = silver_invalid_issues_path or DEFAULT_SILVER_INVALID
    df = _read_any(silver_invalid_issues_path)

    if df.empty:
        logger.info("No invalid/missing issues found.")
        return df

    # Prefer explicit classification if present
    if "row_status" in df.columns:
        df = df[df["row_status"].astype(str).str.upper() == "MISSING"].copy()
    else:
        # Fallback: missing resolved_at
        df = df[df["resolved_at"].isna() | (df["resolved_at"].astype(str).str.strip() == "")].copy()

    if df.empty:
        logger.info("No backlog issues found.")
        return df

    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)

    holidays = _load_holidays(holidays_path, silver_calendar_path)

    now = pd.Timestamp.utcnow()
    df["as_of_ts"] = now
    df["sla_target_hours"] = df["priority"].apply(sla_expected_hours)

    df["age_business_hours"] = df.apply(
        lambda r: calculate_business_hours(r["created_at"], now, holidays),
        axis=1,
    )

    df["is_overdue"] = df["age_business_hours"] > df["sla_target_hours"]

    gold_backlog_path = gold_backlog_path or DEFAULT_GOLD_BACKLOG
    _ensure_parent(gold_backlog_path)
    df.to_csv(gold_backlog_path, index=False)

    logger.info(f"Gold BACKLOG saved: {gold_backlog_path} | rows={len(df)}")
    logger.info("Finished build_gold_backlog")
    return df


# =========================================================
# ENTRYPOINT (python -m src.gold.build_gold)
# =========================================================
def main() -> None:
    """Runs Gold layer end-to-end."""
    # resolved + aggregations
    build_gold_resolved(
        silver_valid_issues_path=DEFAULT_SILVER_VALID,
        silver_calendar_path=DEFAULT_SILVER_CALENDAR,
        holidays_path=DEFAULT_BRONZE_HOLIDAYS,
    )

    # backlog
    build_gold_backlog(
        silver_invalid_issues_path=DEFAULT_SILVER_INVALID,
        silver_calendar_path=DEFAULT_SILVER_CALENDAR,
        holidays_path=DEFAULT_BRONZE_HOLIDAYS,
    )


if __name__ == "__main__":
    main()
