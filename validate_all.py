from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------
# Config / Paths
# ---------------------------
ROOT = Path(__file__).resolve().parent

BRONZE_ISSUES = ROOT / "data" / "bronze" / "jira_issues_raw.json"
BRONZE_HOLIDAYS = ROOT / "data" / "bronze" / "bronze_holidays.csv"

SILVER_VALID = ROOT / "data" / "silver" / "silver_issues.csv"
SILVER_INVALID = ROOT / "data" / "silver" / "silver_issues_invalid.csv"
SILVER_DQ = ROOT / "data" / "silver" / "silver_dq.json"
SILVER_CALENDAR = ROOT / "data" / "silver" / "silver_calendar.csv"

GOLD_ISSUES = ROOT / "data" / "gold" / "gold_sla_issues.csv"
GOLD_BY_ANALYST = ROOT / "data" / "gold" / "gold_sla_by_analyst.csv"
GOLD_BY_TYPE = ROOT / "data" / "gold" / "gold_sla_by_issue_type.csv"
GOLD_BACKLOG = ROOT / "data" / "gold" / "gold_sla_backlog.csv"


SLA_TABLE = {"High": 24, "Medium": 72, "Low": 120}
DONE_STATUSES = {"Done", "Resolved"}


# ---------------------------
# Minimal assert framework
# ---------------------------
@dataclass
class CheckResult:
    name: str
    ok: bool
    details: str = ""


def ok(name: str, details: str = "") -> CheckResult:
    return CheckResult(name=name, ok=True, details=details)


def fail(name: str, details: str = "") -> CheckResult:
    return CheckResult(name=name, ok=False, details=details)


def print_results(results: list[CheckResult]) -> None:
    passed = sum(r.ok for r in results)
    total = len(results)
    print("\n==============================")
    print(f"VALIDATION SUMMARY: {passed}/{total} passed")
    print("==============================\n")

    for r in results:
        icon = "✅" if r.ok else "❌"
        line = f"{icon} {r.name}"
        if r.details:
            line += f" | {r.details}"
        print(line)


def must_exist(path: Path, label: str) -> CheckResult:
    if not path.exists():
        return fail(f"{label} exists", f"NOT FOUND: {path}")
    return ok(f"{label} exists", f"{path} ({path.stat().st_size} bytes)")


# ---------------------------
# Bronze checks
# ---------------------------
def read_bronze_issues(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    issues = raw.get("issues", [])
    if not isinstance(issues, list):
        return []
    return [x for x in issues if isinstance(x, dict)]


def is_iso_like(s: Any) -> bool:
    if s is None or not isinstance(s, str) or not s.strip():
        return False
    v = s.strip()
    try:
        # normalize Z
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        datetime.fromisoformat(v)
        return True
    except Exception:
        return False


def bronze_checks(results: list[CheckResult]) -> dict[str, Any]:
    results.append(must_exist(BRONZE_ISSUES, "Bronze issues JSON"))
    results.append(must_exist(BRONZE_HOLIDAYS, "Bronze holidays CSV"))

    if not BRONZE_ISSUES.exists():
        return {"issues": []}

    issues = read_bronze_issues(BRONZE_ISSUES)
    results.append(ok("Bronze issues parsed", f"count={len(issues)}"))

    # expected structure: id, issue_type, status, priority, assignee(list), timestamps(list)
    missing_id = sum(1 for it in issues if not it.get("id"))
    results.append(ok("Bronze issues have id", f"missing_id={missing_id}") if missing_id == 0 else fail("Bronze issues have id", f"missing_id={missing_id}"))

    created_bad = 0
    created_null = 0
    resolved_bad = 0
    resolved_null = 0

    for it in issues:
        ts = it.get("timestamps")
        ts = ts[0] if isinstance(ts, list) and ts else (ts if isinstance(ts, dict) else {})
        c = ts.get("created_at")
        r = ts.get("resolved_at")

        if c is None or (isinstance(c, str) and not c.strip()):
            created_null += 1
        elif not is_iso_like(c):
            created_bad += 1

        if r is None or (isinstance(r, str) and not r.strip()):
            resolved_null += 1
        elif not is_iso_like(r):
            resolved_bad += 1

    results.append(ok("Bronze created_at quality", f"missing={created_null}, invalid={created_bad}"))
    results.append(ok("Bronze resolved_at quality", f"missing={resolved_null}, invalid={resolved_bad}"))

    # holidays sanity
    if BRONZE_HOLIDAYS.exists():
        h = pd.read_csv(BRONZE_HOLIDAYS)
        has_date = "date" in h.columns
        years = sorted(h["year"].unique().tolist()) if "year" in h.columns else []
        results.append(ok("Bronze holidays schema", f"cols={list(h.columns)}") if has_date else fail("Bronze holidays schema", f"missing 'date' col; cols={list(h.columns)}"))
        if has_date:
            results.append(ok("Bronze holidays range", f"min={h['date'].min()} max={h['date'].max()} years={years}"))

    return {
        "issues": issues,
        "bronze_created_bad": created_bad,
        "bronze_resolved_bad": resolved_bad,
        "bronze_resolved_missing": resolved_null,
    }


# ---------------------------
# Silver checks
# ---------------------------
def silver_checks(results: list[CheckResult], bronze_total: int) -> dict[str, Any]:
    results.append(must_exist(SILVER_VALID, "Silver valid issues CSV"))
    results.append(must_exist(SILVER_INVALID, "Silver invalid issues CSV"))
    results.append(must_exist(SILVER_DQ, "Silver DQ JSON"))

    if not (SILVER_VALID.exists() and SILVER_INVALID.exists()):
        return {}

    v = pd.read_csv(SILVER_VALID)
    i = pd.read_csv(SILVER_INVALID)

    total = len(v) + len(i)
    results.append(ok("Silver row count closes with Bronze", f"valid={len(v)} invalid/missing={len(i)} total={total} bronze_total={bronze_total}") if total == bronze_total else fail("Silver row count closes with Bronze", f"valid={len(v)} invalid/missing={len(i)} total={total} bronze_total={bronze_total}"))

    # issue_key must be filled (your chosen rule: key fallback to id)
    if "issue_key" in v.columns:
        nulls = int(v["issue_key"].isna().sum())
        blanks = int((v["issue_key"].astype(str).str.strip() == "").sum())
        results.append(ok("Silver issue_key filled", f"nulls={nulls}, blanks={blanks}") if (nulls == 0 and blanks == 0) else fail("Silver issue_key filled", f"nulls={nulls}, blanks={blanks}"))
    else:
        results.append(fail("Silver has issue_key column", f"cols={list(v.columns)}"))

    # valid should have created_at/resolved_at non-empty
    for col in ["created_at", "resolved_at"]:
        if col in v.columns:
            empty = int((v[col].astype(str).str.strip() == "").sum())
            results.append(ok(f"Silver valid {col} populated", f"empty={empty}") if empty == 0 else fail(f"Silver valid {col} populated", f"empty={empty}"))
        else:
            results.append(fail(f"Silver valid has {col}", f"cols={list(v.columns)}"))

    # DQ json
    if SILVER_DQ.exists():
        dq = json.loads(SILVER_DQ.read_text(encoding="utf-8"))
        results.append(ok("Silver DQ json readable", f"keys={list(dq.keys())}"))
    else:
        dq = {}

    return {"silver_valid": v, "silver_invalid": i, "silver_dq": dq}


# ---------------------------
# Calendar checks
# ---------------------------
def calendar_checks(results: list[CheckResult]) -> dict[str, Any]:
    results.append(must_exist(SILVER_CALENDAR, "Silver calendar CSV"))

    if not SILVER_CALENDAR.exists():
        return {}

    cal = pd.read_csv(SILVER_CALENDAR)

    has_date = "date" in cal.columns
    has_biz = "is_business_day" in cal.columns
    has_holiday = "is_holiday" in cal.columns

    if not has_date:
        results.append(fail("Calendar has date column", f"cols={list(cal.columns)}"))
        return {}
    if not has_biz:
        results.append(fail("Calendar has is_business_day", f"cols={list(cal.columns)}"))
        return {}
    if not has_holiday:
        # not strictly required, but useful
        results.append(ok("Calendar has is_holiday", "missing (optional)"))
    else:
        results.append(ok("Calendar has is_holiday", "ok"))

    cal["date"] = pd.to_datetime(cal["date"], errors="coerce").dt.date
    bad_dates = int(cal["date"].isna().sum())
    results.append(ok("Calendar date parseable", f"bad_dates={bad_dates}") if bad_dates == 0 else fail("Calendar date parseable", f"bad_dates={bad_dates}"))

    # basic business day sanity: weekends should be False
    if "weekday" in cal.columns:
        wknd = cal[cal["weekday"].isin(["Sat", "Sun", "Saturday", "Sunday"])]
        if not wknd.empty:
            wknd_true = int(wknd["is_business_day"].astype(str).str.lower().isin(["true", "1", "yes", "y"]).sum())
            results.append(ok("Calendar weekends excluded", f"weekend_business_day_true={wknd_true}") if wknd_true == 0 else fail("Calendar weekends excluded", f"weekend_business_day_true={wknd_true}"))
    else:
        results.append(ok("Calendar weekend check", "skipped (no weekday col)"))

    return {"calendar": cal}


# ---------------------------
# Gold checks
# ---------------------------
def gold_checks(results: list[CheckResult]) -> dict[str, Any]:
    results.append(must_exist(GOLD_ISSUES, "Gold issues CSV"))
    results.append(must_exist(GOLD_BY_ANALYST, "Gold by analyst CSV"))
    results.append(must_exist(GOLD_BY_TYPE, "Gold by issue type CSV"))
    # backlog may not exist depending on build; but after your fix it should
    if GOLD_BACKLOG.exists():
        results.append(ok("Gold backlog CSV exists", f"{GOLD_BACKLOG}"))
    else:
        results.append(ok("Gold backlog CSV exists", "missing (will fail if backlog expected)"))

    if not GOLD_ISSUES.exists():
        return {}

    g = pd.read_csv(GOLD_ISSUES)

    # issue_key must be filled
    if "issue_key" in g.columns:
        nulls = int(g["issue_key"].isna().sum())
        blanks = int((g["issue_key"].astype(str).str.strip() == "").sum())
        results.append(ok("Gold issue_key filled", f"nulls={nulls}, blanks={blanks}") if (nulls == 0 and blanks == 0) else fail("Gold issue_key filled", f"nulls={nulls}, blanks={blanks}"))
    else:
        results.append(fail("Gold has issue_key column", f"cols={list(g.columns)}"))

    # resolved dataset only: Done/Resolved
    if "status" in g.columns:
        bad = int((~g["status"].isin(list(DONE_STATUSES))).sum())
        results.append(ok("Gold status only Done/Resolved", f"bad_rows={bad}") if bad == 0 else fail("Gold status only Done/Resolved", f"bad_rows={bad}"))
    else:
        results.append(ok("Gold status check", "skipped (no status col)"))

    # resolution_hours integrity
    hrs_col = "resolution_hours" if "resolution_hours" in g.columns else None
    if hrs_col is None:
        results.append(fail("Gold has resolution_hours", f"cols={list(g.columns)}"))
        return {"gold": g}

    g[hrs_col] = pd.to_numeric(g[hrs_col], errors="coerce")
    nan = int(g[hrs_col].isna().sum())
    neg = int((g[hrs_col] < 0).sum())
    results.append(ok("Gold resolution_hours no NaN", f"nan={nan}") if nan == 0 else fail("Gold resolution_hours no NaN", f"nan={nan}"))
    results.append(ok("Gold resolution_hours non-negative", f"neg={neg}") if neg == 0 else fail("Gold resolution_hours non-negative", f"neg={neg}"))

    # SLA met rule consistency
    if "priority" in g.columns and "sla_met" in g.columns:
        mism = 0
        checked = 0

        def pri(x: Any) -> str | None:
            if pd.isna(x):
                return None
            return str(x).strip().title()

        def to_bool(x: Any) -> bool | None:
            if pd.isna(x):
                return None
            s = str(x).strip().lower()
            if s in ["true", "1", "yes", "y", "ok", "met", "sla_met"]:
                return True
            if s in ["false", "0", "no", "n", "breached", "estourou", "violated"]:
                return False
            return None

        for _, r in g.iterrows():
            p = pri(r["priority"])
            if p not in SLA_TABLE:
                continue
            hrs = r[hrs_col]
            if pd.isna(hrs):
                continue
            should_met = hrs <= SLA_TABLE[p]
            met = to_bool(r["sla_met"])
            if met is None:
                continue
            checked += 1
            if met != should_met:
                mism += 1

        results.append(ok("Gold sla_met matches thresholds", f"checked={checked} mismatches={mism}") if mism == 0 and checked > 0 else fail("Gold sla_met matches thresholds", f"checked={checked} mismatches={mism}"))
    else:
        results.append(ok("Gold sla threshold check", "skipped (missing priority/sla_met)"))

    return {"gold": g}


# ---------------------------
# Aggregation consistency checks
# ---------------------------
def aggregation_checks(results: list[CheckResult]) -> None:
    if not (GOLD_ISSUES.exists() and GOLD_BY_ANALYST.exists() and GOLD_BY_TYPE.exists()):
        results.append(ok("Aggregation consistency", "skipped (missing files)"))
        return

    g = pd.read_csv(GOLD_ISSUES)
    by_a = pd.read_csv(GOLD_BY_ANALYST)
    by_t = pd.read_csv(GOLD_BY_TYPE)

    # analyst qty consistency (if columns exist)
    if "assignee_name" in g.columns and "qtd" in by_a.columns and "assignee_name" in by_a.columns:
        rec = g.groupby("assignee_name").size().reset_index(name="qtd_calc")
        merged = rec.merge(by_a[["assignee_name", "qtd"]], on="assignee_name", how="outer")
        merged["qtd"] = pd.to_numeric(merged["qtd"], errors="coerce")
        merged["qtd_calc"] = pd.to_numeric(merged["qtd_calc"], errors="coerce")
        bad = merged[(merged["qtd"].fillna(-1) != merged["qtd_calc"].fillna(-2))]
        results.append(ok("Gold by_analyst qtd matches", f"rows={len(by_a)} mismatches=0") if bad.empty else fail("Gold by_analyst qtd matches", f"mismatches={len(bad)} sample={bad.head(3).to_dict(orient='records')}"))
    else:
        results.append(ok("Gold by_analyst qtd matches", "skipped (missing columns)"))

    # issue_type qty consistency
    if "issue_type" in g.columns and "qtd" in by_t.columns and "issue_type" in by_t.columns:
        rec = g.groupby("issue_type").size().reset_index(name="qtd_calc")
        merged = rec.merge(by_t[["issue_type", "qtd"]], on="issue_type", how="outer")
        merged["qtd"] = pd.to_numeric(merged["qtd"], errors="coerce")
        merged["qtd_calc"] = pd.to_numeric(merged["qtd_calc"], errors="coerce")
        bad = merged[(merged["qtd"].fillna(-1) != merged["qtd_calc"].fillna(-2))]
        results.append(ok("Gold by_issue_type qtd matches", f"rows={len(by_t)} mismatches=0") if bad.empty else fail("Gold by_issue_type qtd matches", f"mismatches={len(bad)} sample={bad.head(3).to_dict(orient='records')}"))
    else:
        results.append(ok("Gold by_issue_type qtd matches", "skipped (missing columns)"))


# ---------------------------
# Backlog check
# ---------------------------
def backlog_checks(results: list[CheckResult], silver_invalid: pd.DataFrame | None) -> None:
    # expected backlog count comes from silver_invalid rows where row_status == MISSING (or resolved_at empty)
    expected = None
    if silver_invalid is not None and not silver_invalid.empty:
        if "row_status" in silver_invalid.columns:
            expected = int((silver_invalid["row_status"].astype(str).str.upper() == "MISSING").sum())
        else:
            expected = int((silver_invalid["resolved_at"].isna() | (silver_invalid["resolved_at"].astype(str).str.strip() == "")).sum())

    if not GOLD_BACKLOG.exists():
        if expected is None:
            results.append(ok("Gold backlog exists", "skipped (cannot infer expected)"))
        else:
            results.append(fail("Gold backlog exists", f"missing file; expected~{expected} rows"))
        return

    b = pd.read_csv(GOLD_BACKLOG)
    if expected is not None:
        results.append(ok("Gold backlog rowcount matches expected", f"gold={len(b)} expected~{expected}") if len(b) == expected else fail("Gold backlog rowcount matches expected", f"gold={len(b)} expected~{expected}"))
    else:
        results.append(ok("Gold backlog rowcount", f"rows={len(b)} (expected unknown)"))


# ---------------------------
# Entrypoint
# ---------------------------
def main() -> int:
    results: list[CheckResult] = []

    # files exist
    results.append(ok("Repo root", str(ROOT)))

    # Bronze
    bronze_info = bronze_checks(results)
    bronze_total = len(bronze_info.get("issues", []))

    # Silver
    silver_info = silver_checks(results, bronze_total=bronze_total)
    silver_invalid = silver_info.get("silver_invalid") if silver_info else None

    # Calendar
    calendar_checks(results)

    # Gold
    gold_checks(results)

    # Aggregations
    aggregation_checks(results)

    # Backlog
    backlog_checks(results, silver_invalid=silver_invalid)

    print_results(results)

    failed = [r for r in results if not r.ok]
    if failed:
        print("\nFAILED CHECKS:")
        for r in failed:
            print(f" - {r.name}: {r.details}")
        return 1

    print("\n✅ All validations passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
