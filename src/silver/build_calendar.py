from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Set

import pandas as pd
from pandas.errors import EmptyDataError

from src.common.logger import get_logger

logger = get_logger(__name__, layer="SILVER")


def _load_holidays(bronze_holidays_path: str | Path | None) -> Set[date]:
    """
    Accepts:
      - None
      - path to JSON/CSV

    Supported formats:
      - JSON: [{"date":"YYYY-MM-DD"}, ...]
      - JSON: {"holidays":[{"date":"YYYY-MM-DD"}, ...]}
      - JSON: ["YYYY-MM-DD", ...]
      - CSV: column "date"
    """
    if bronze_holidays_path is None:
        return set()

    p = Path(bronze_holidays_path)

    if not p.exists():
        logger.warning(f"bronze_holidays_path not found: {p} (ignoring)")
        return set()

    # ----------------------------
    # CSV
    # ----------------------------
    if p.suffix.lower() == ".csv":
        try:
            df = pd.read_csv(p)
        except EmptyDataError:
            return set()

        if "date" not in df.columns:
            return set()

        dts = pd.to_datetime(df["date"], errors="coerce").dropna().dt.date.tolist()
        return set(dts)

    # ----------------------------
    # JSON
    # ----------------------------
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(f"Could not parse holidays JSON: {p} (ignoring)")
        return set()

    items = None
    if isinstance(data, dict) and isinstance(data.get("holidays"), list):
        items = data["holidays"]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    holidays: Set[date] = set()

    for it in items:
        if isinstance(it, str):
            dt = pd.to_datetime(it, errors="coerce")
            if not pd.isna(dt):
                holidays.add(dt.date())
        elif isinstance(it, dict):
            v = it.get("date") or it.get("holiday_date") or it.get("data")
            dt = pd.to_datetime(v, errors="coerce")
            if not pd.isna(dt):
                holidays.add(dt.date())

    return holidays


def build_calendar_to_silver(
    *,
    silver_valid_issues_path: str | Path,
    bronze_holidays_path: str | Path | None = None,
) -> Path:
    """
    Creates data/silver/silver_calendar.csv with:
      - date
      - is_weekend
      - is_holiday
      - is_business_day
      - business_hours_day (8 if business day else 0)
    """
    silver_valid_issues_path = Path(silver_valid_issues_path)

    silver_dir = Path("data") / "silver"
    silver_dir.mkdir(parents=True, exist_ok=True)

    out_path = silver_dir / "silver_calendar.csv"

    # ----------------------------
    # Read Silver issues
    # ----------------------------
    try:
        df = pd.read_csv(silver_valid_issues_path)
    except EmptyDataError:
        logger.warning(
            f"Silver issues is empty: {silver_valid_issues_path}. Writing empty calendar."
        )
        pd.DataFrame(
            columns=[
                "date",
                "is_weekend",
                "is_holiday",
                "is_business_day",
                "business_hours_day",
            ]
        ).to_csv(out_path, index=False)
        return out_path

    if df.empty or "created_at" not in df.columns:
        logger.warning(
            "Silver issues empty or missing created_at. Writing empty calendar."
        )
        pd.DataFrame(
            columns=[
                "date",
                "is_weekend",
                "is_holiday",
                "is_business_day",
                "business_hours_day",
            ]
        ).to_csv(out_path, index=False)
        return out_path

    # ----------------------------
    # Parse datetimes (UTC safe)
    # ----------------------------
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)

    if "resolved_at" in df.columns:
        df["resolved_at"] = pd.to_datetime(df["resolved_at"], errors="coerce", utc=True)
    else:
        df["resolved_at"] = pd.NaT

    min_dt = df["created_at"].min()
    max_dt = df["resolved_at"].max()

    if pd.isna(max_dt):
        max_dt = df["created_at"].max()

    if pd.isna(min_dt) or pd.isna(max_dt):
        logger.warning(
            "Could not determine date range from issues. Writing empty calendar."
        )
        pd.DataFrame(
            columns=[
                "date",
                "is_weekend",
                "is_holiday",
                "is_business_day",
                "business_hours_day",
            ]
        ).to_csv(out_path, index=False)
        return out_path

    start = min_dt.date()
    end = max_dt.date()

    # ----------------------------
    # Load holidays
    # ----------------------------
    holidays = _load_holidays(bronze_holidays_path)
    logger.info(f"Holidays loaded: {len(holidays)}")

    # ----------------------------
    # Build calendar
    # ----------------------------
    days = pd.date_range(start=start, end=end, freq="D")
    cal = pd.DataFrame({"date": days.date})

    cal["weekday"] = pd.to_datetime(cal["date"]).dt.weekday  # 0=Mon..6=Sun
    cal["is_weekend"] = cal["weekday"].isin([5, 6])
    cal["is_holiday"] = cal["date"].isin(holidays)

    # Business day = not weekend and not holiday
    cal["is_business_day"] = (~cal["is_weekend"]) & (~cal["is_holiday"])

    # 8 business-hours per business day
    cal["business_hours_day"] = cal["is_business_day"].apply(lambda x: 8 if x else 0)

    cal = cal.drop(columns=["weekday"])

    cal.to_csv(out_path, index=False)

    logger.info(
        f"✅ Silver CALENDAR saved: {out_path} | rows={len(cal)} | range={start}..{end}"
    )

    return out_path
