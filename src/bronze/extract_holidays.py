"""
src/bronze/extract_holidays.py

Robust holiday extraction for Brazilian national holidays.

Goal:
- Ensure national holidays exist (e.g., 2025-01-01) so SLA does not overcount.

Key robustness:
- Issues dataset may be CSV/Parquet/Excel/JSON (sometimes JSON is nested or not tabular).
- If the issues dataset cannot be parsed, DO NOT FAIL the pipeline.
  We fall back to a safe year range [2024, 2025, 2026] and proceed.

Environment overrides:
- BRONZE_DIR: folder where bronze files live (default: data/bronze)
- BRONZE_ISSUES_PATH: explicit path to bronze issues dataset (optional)
- BRONZE_HOLIDAYS_PATH: output file path (default: data/bronze/bronze_holidays.csv)
- HOLIDAYS_YEAR_FROM / HOLIDAYS_YEAR_TO: force a year range (optional)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from src.common.logger import get_logger

logger = get_logger(__name__, layer="BRONZE")


@dataclass(frozen=True)
class Paths:
    bronze_dir: str = os.getenv("BRONZE_DIR", "data/bronze")
    bronze_issues_path: str | None = os.getenv("BRONZE_ISSUES_PATH") or None
    bronze_holidays_path: str = os.getenv("BRONZE_HOLIDAYS_PATH", "data/bronze/bronze_holidays.csv")


SUPPORTED_EXTS = (".csv", ".parquet", ".xlsx", ".xls", ".json", ".txt")


def _read_any_tabular(path: str | Path) -> pd.DataFrame:
    """
    Read tabular formats directly with pandas (CSV/Parquet/Excel).
    JSON is handled separately because it may not be tabular.
    """
    p = Path(path)
    suf = p.suffix.lower()

    if suf == ".parquet":
        return pd.read_parquet(p)
    if suf in (".csv", ".txt"):
        return pd.read_csv(p)
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(p)

    raise ValueError(f"Unsupported tabular type for _read_any_tabular: {p.suffix} ({p})")


def _read_json_as_dataframe(path: str | Path) -> pd.DataFrame:
    """
    Attempts to parse JSON in multiple common formats:
    - list of dicts: [ {..}, {..} ]
    - dict with an 'issues' list: { "issues": [ {..}, ... ] }
    - JSON lines (jsonl): each line is an object

    If none works, raises.
    """
    p = Path(path)

    # 1) Try pandas normal JSON
    try:
        df = pd.read_json(p)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception:
        pass

    # 2) Try JSON lines
    try:
        df = pd.read_json(p, lines=True)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception:
        pass

    # 3) Try Python json module
    text = p.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        raise ValueError("JSON file is empty")

    obj = json.loads(text)

    # list of dicts
    if isinstance(obj, list):
        # keep only dict items
        rows = [x for x in obj if isinstance(x, dict)]
        if rows:
            return pd.DataFrame(rows)
        raise ValueError("JSON list does not contain dict rows")

    # dict with issues
    if isinstance(obj, dict):
        if "issues" in obj and isinstance(obj["issues"], list):
            rows = [x for x in obj["issues"] if isinstance(x, dict)]
            if rows:
                return pd.DataFrame(rows)
            raise ValueError("JSON dict['issues'] does not contain dict rows")

        # fallback: try to normalize dict (may still be too nested)
        try:
            return pd.json_normalize(obj)
        except Exception as e:
            raise ValueError(f"Could not normalize JSON dict: {e}") from e

    raise ValueError(f"Unsupported JSON structure: {type(obj)}")


def _find_bronze_issues_file(bronze_dir: str) -> Path | None:
    """
    Find the bronze issues dataset automatically in the bronze dir.
    Prefer filenames containing 'issue' or 'jira'. Otherwise choose the largest file.
    """
    d = Path(bronze_dir)
    if not d.exists():
        logger.warning(f"Bronze directory not found: {d}")
        return None

    files = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    if not files:
        return None

    preferred = [p for p in files if ("issue" in p.name.lower() or "jira" in p.name.lower())]
    if preferred:
        preferred.sort(key=lambda p: p.stat().st_size, reverse=True)
        return preferred[0]

    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return files[0]


def _detect_years_from_issues_df(df: pd.DataFrame) -> list[int]:
    """
    Detect year range from common created/resolved timestamp columns.
    Returns a list of years to fetch.

    If columns are not found or values are empty, returns a safe fallback.
    """
    candidates: list[int] = []

    for col in [
        "created_at",
        "resolved_at",
        "created",
        "resolved",
        "resolutiondate",
        "created_date",
        "resolved_date",
    ]:
        if col in df.columns:
            s = pd.to_datetime(df[col], errors="coerce", utc=True).dropna()
            if not s.empty:
                candidates.append(int(s.dt.year.min()))
                candidates.append(int(s.dt.year.max()))

    if not candidates:
        return [2025, 2026]

    y_min = min(candidates) - 1  # buffer
    y_max = max(candidates) + 1  # buffer
    return list(range(y_min, y_max + 1))


def _forced_years() -> list[int] | None:
    yf = os.getenv("HOLIDAYS_YEAR_FROM")
    yt = os.getenv("HOLIDAYS_YEAR_TO")
    if yf and yt:
        a = int(yf)
        b = int(yt)
        return list(range(min(a, b), max(a, b) + 1))
    return None


def _fetch_holidays_brasilapi(year: int, timeout_s: int = 30) -> list[dict]:
    url = f"https://brasilapi.com.br/api/feriados/v1/{year}"
    r = requests.get(url, timeout=timeout_s, headers={"Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response for {url}: {type(data)}")
    return data


def extract_holidays_to_bronze(paths: Paths = Paths()) -> pd.DataFrame:
    """
    Pipeline step.
    Writes:
      data/bronze/bronze_holidays.csv
    Columns:
      - date (date)
      - name (str)
      - type (str)
      - source (str)
      - year (int)
    """
    logger.info("Starting extract_holidays_to_bronze")

    # If user forced year range, use it (no need to parse issues)
    forced = _forced_years()
    if forced is not None:
        years = forced
        logger.info(f"Using forced holiday year range: {years}")
    else:
        # Locate issues dataset
        issues_path: Path | None = None

        if paths.bronze_issues_path:
            p = Path(paths.bronze_issues_path)
            if p.exists():
                issues_path = p
            else:
                logger.warning(f"BRONZE_ISSUES_PATH set but file not found: {p}")

        if issues_path is None:
            issues_path = _find_bronze_issues_file(paths.bronze_dir)

        # Try to detect years from issues; if it fails, do not crash.
        years = [2024, 2025, 2026]  # safe default that covers your failing example
        if issues_path is not None:
            logger.info(f"Using issues dataset for year detection: {issues_path}")
            try:
                if issues_path.suffix.lower() == ".json":
                    issues_df = _read_json_as_dataframe(issues_path)
                else:
                    issues_df = _read_any_tabular(issues_path)

                years = _detect_years_from_issues_df(issues_df)
            except Exception as e:
                logger.warning(
                    f"Could not parse issues dataset for year detection ({issues_path}). "
                    f"Falling back to years {years}. Error: {e}"
                )
        else:
            logger.warning(f"Could not locate bronze issues dataset. Falling back to years {years}.")

    logger.info(f"Holiday years to fetch: {years}")

    rows: list[dict] = []
    for y in years:
        items = _fetch_holidays_brasilapi(y)
        for it in items:
            rows.append(
                {
                    "date": it.get("date"),
                    "name": it.get("name"),
                    "type": it.get("type", "national"),
                    "source": "brasilapi",
                    "year": y,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("Holiday extraction returned empty dataset (unexpected).")

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df.dropna(subset=["date"]).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)

    out_path = Path(paths.bronze_holidays_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8")
    logger.info(f"Holidays written: {out_path} | rows={len(df)}")

    # sanity check for your example
    jan1_2025 = datetime(2025, 1, 1).date()
    if jan1_2025 not in set(df["date"].tolist()):
        logger.warning("Sanity check: 2025-01-01 not found in holidays output! (unexpected)")

    return df


if __name__ == "__main__":
    extract_holidays_to_bronze()
