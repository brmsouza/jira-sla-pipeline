"""
SLA Dashboard (CSV-only)

SILVER:
  - data/silver/silver_issues_invalid.csv
  - data/silver/silver_dq.json

GOLD (CSV):
  - data/gold/gold_sla_issues.csv          (resolved issues)        [REQUIRED]
  - data/gold/gold_sla_by_analyst.csv      (avg SLA by analyst)     [REQUIRED]
  - data/gold/gold_sla_by_issue_type.csv   (avg SLA by issue type)  [REQUIRED]
  - data/gold/gold_sla_backlog.csv         (open issues / in-progress) [OPTIONAL]

AUDIT / GOVERNANCE (optional):
  - data/audit/run_log.csv
  - data/audit/lineage.json

BRONZE:
  - data/bronze/jira_issues_raw.json       (raw issues JSON / JSONL)

Run:
  python -m streamlit run src/dashboard/app.py
"""

from __future__ import annotations

# ============================================================
# FIX Streamlit import path: allow "from src...." to work
# ============================================================
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ============================================================

from datetime import date, datetime
import io
import json
import zipfile
from typing import Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.io as pio
import streamlit as st

from src.common.governance import mask_name
from src.common.logger import get_logger


# --- Visual: clean / Windows-ish (light) ---
pio.templates.default = "plotly_white"

logger = get_logger(__name__, layer="dashboard")

# ================================
# Paths
# ================================
BASE_DIR = Path(__file__).resolve().parents[2]

# BRONZE
BRONZE_RAW_PATH = BASE_DIR / "data" / "bronze" / "jira_issues_raw.json"

# REQUIRED outputs
GOLD_RESOLVED_PATH = BASE_DIR / "data" / "gold" / "gold_sla_issues.csv"
GOLD_BY_ANALYST_PATH = BASE_DIR / "data" / "gold" / "gold_sla_by_analyst.csv"
GOLD_BY_ISSUE_TYPE_PATH = BASE_DIR / "data" / "gold" / "gold_sla_by_issue_type.csv"

# OPTIONAL outputs
GOLD_BACKLOG_PATH = BASE_DIR / "data" / "gold" / "gold_sla_backlog.csv"

SILVER_INVALID_PATH = BASE_DIR / "data" / "silver" / "silver_issues_invalid.csv"
SILVER_DQ_JSON_PATH = BASE_DIR / "data" / "silver" / "silver_dq.json"
SILVER_DQ_CSV_PATH = BASE_DIR / "data" / "silver" / "silver_dq_report.csv"  # optional fallback

AUDIT_RUN_LOG_PATH = BASE_DIR / "data" / "audit" / "run_log.csv"
AUDIT_LINEAGE_PATH = BASE_DIR / "data" / "audit" / "lineage.json"


# ================================
# Streamlit config
# ================================
st.set_page_config(
    page_title="SLA Monitoring Dashboard (CSV)",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Minimal, stable CSS (typography + compact labels)
st.markdown(
    """
<style>
html, body, [class*="css"]  {
  font-family: "Segoe UI", system-ui, -apple-system, Arial, sans-serif;
}
.block-container { padding-top: 1.0rem; padding-bottom: 2.0rem; }

.small-muted {
  font-size: 0.88rem;
  opacity: 0.78;
  line-height: 1.25rem;
}
.small-strong {
  font-size: 0.92rem;
  font-weight: 600;
  opacity: 0.88;
}
.kpi-note {
  font-size: 0.86rem;
  opacity: 0.78;
  line-height: 1.20rem;
}
</style>
""",
    unsafe_allow_html=True,
)


# ================================
# Helpers
# ================================
def _file_info(path: Path) -> Tuple[bool, int, Optional[datetime]]:
    if not path.exists():
        return (False, 0, None)
    stat = path.stat()
    return (True, int(stat.st_size), datetime.fromtimestamp(stat.st_mtime))


def _try_has_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401

        return True
    except Exception:
        return False


HAS_PYARROW = _try_has_pyarrow()


def render_df(df: pd.DataFrame, *, preview_rows: int = 200) -> None:
    if df.empty:
        st.info("No data found.")
        return

    preview = df.head(preview_rows).copy()
    if HAS_PYARROW:
        st.dataframe(preview, width="stretch", hide_index=True)
        if len(df) > preview_rows:
            st.caption(f"Previewing first {preview_rows} rows. Use download for full file.")
        return

    st.warning("PyArrow is not available. Showing a safe preview table instead.")
    st.table(preview)
    if len(df) > preview_rows:
        st.caption(f"Previewing first {preview_rows} rows. Use download for full file.")


def render_download_button(path: Path, label: str, filename: str) -> None:
    if not path.exists() or path.stat().st_size == 0:
        st.button(label, disabled=True, width="stretch")
        return

    st.download_button(
        label=label,
        data=path.read_bytes(),
        file_name=filename,
        mime="text/csv",
        width="stretch",
    )


def build_required_zip_bytes() -> Optional[bytes]:
    required = [
        (GOLD_RESOLVED_PATH, "gold_sla_issues.csv"),
        (GOLD_BY_ANALYST_PATH, "gold_sla_by_analyst.csv"),
        (GOLD_BY_ISSUE_TYPE_PATH, "gold_sla_by_issue_type.csv"),
    ]

    for p, _name in required:
        if (not p.exists()) or p.stat().st_size == 0:
            return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p, name in required:
            zf.writestr(name, p.read_bytes())
    return buf.getvalue()


@st.cache_data(show_spinner=False)
def read_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


@st.cache_data(show_spinner=False)
def read_json_or_empty(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to read JSON: {path} | {e}")
        return {}


@st.cache_data(show_spinner=False)
def count_bronze_records(path: Path) -> int:
    """
    Counts records in Bronze raw file robustly.
    We NEVER count file lines unless JSON parsing fails.
    """
    if not path.exists() or path.stat().st_size == 0:
        return 0

    try:
        txt = path.read_text(encoding="utf-8-sig", errors="replace")
        if not txt.strip():
            return 0

        obj = json.loads(txt)

        if isinstance(obj, list):
            return int(len(obj))

        if isinstance(obj, dict):
            common_list_keys = ["issues", "data", "values", "results", "items", "rows", "records"]
            for k in common_list_keys:
                v = obj.get(k)
                if isinstance(v, list):
                    return int(len(v))
            return 1

        return 0

    except Exception:
        # JSONL fallback: count valid JSON objects per non-empty line
        try:
            count = 0
            with path.open("r", encoding="utf-8-sig", errors="replace") as f:
                for line in f:
                    ln = line.strip()
                    if not ln:
                        continue
                    try:
                        json.loads(ln)
                        count += 1
                    except Exception:
                        continue
            return int(count)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to count bronze records: {path} | {e}")
            return 0


def _fmt_dt_utc(x) -> str:
    if x is None:
        return "N/A"
    try:
        ts = pd.to_datetime(x, utc=True, errors="coerce")
        if pd.isna(ts):
            return "N/A"
        return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "N/A"


def get_last_run_summary(run_log: pd.DataFrame) -> dict:
    if run_log.empty:
        return {}

    df = run_log.copy()

    for col in ["start_ts", "end_ts", "started_at", "ended_at", "timestamp"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    sort_col = None
    for col in ["start_ts", "started_at", "timestamp", "end_ts", "ended_at"]:
        if col in df.columns:
            sort_col = col
            break

    if sort_col:
        df = df.sort_values(sort_col, ascending=False)

    last = df.iloc[0].to_dict()

    start_val = None
    for k in ["start_ts", "started_at", "timestamp"]:
        if k in last and pd.notna(last[k]):
            start_val = last[k]
            break

    end_val = None
    for k in ["end_ts", "ended_at"]:
        if k in last and pd.notna(last[k]):
            end_val = last[k]
            break

    status_val = None
    for k in ["status", "run_status", "result"]:
        if k in last and pd.notna(last[k]):
            status_val = str(last[k])
            break

    duration_val = None
    for k in ["duration_s", "duration_seconds", "elapsed_s", "elapsed_seconds"]:
        if k in last and pd.notna(last[k]):
            duration_val = last[k]
            break

    if duration_val is None and start_val is not None and end_val is not None:
        try:
            duration_val = (pd.to_datetime(end_val, utc=True) - pd.to_datetime(start_val, utc=True)).total_seconds()
        except Exception:
            duration_val = None

    return {"start": start_val, "end": end_val, "status": status_val, "duration_s": duration_val}


# ================================
# Load datasets
# ================================
def load_gold_resolved() -> pd.DataFrame:
    df = read_csv_or_empty(GOLD_RESOLVED_PATH)
    if df.empty:
        return df

    df["created_at"] = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True)
    df["resolved_at"] = pd.to_datetime(df.get("resolved_at"), errors="coerce", utc=True)

    if "resolution_hours" not in df.columns and "resolution_business_hours" in df.columns:
        df["resolution_hours"] = pd.to_numeric(df["resolution_business_hours"], errors="coerce")

    if "resolution_hours" not in df.columns:
        df["resolution_hours"] = 0.0
    else:
        df["resolution_hours"] = pd.to_numeric(df["resolution_hours"], errors="coerce").fillna(0.0)

    if "sla_expected_hours" not in df.columns:
        df["sla_expected_hours"] = pd.NA
    else:
        df["sla_expected_hours"] = pd.to_numeric(df["sla_expected_hours"], errors="coerce")

    if "is_sla_met" in df.columns:
        df["sla_met"] = df["is_sla_met"].astype(bool).astype(int)
        df["sla_status"] = df["is_sla_met"].apply(lambda x: "MET" if bool(x) else "BREACHED")
    else:
        df["sla_status"] = df.get("sla_status", "NOT_CALCULATED").fillna("NOT_CALCULATED")
        df["sla_met"] = (df["sla_status"] == "MET").astype(int)

    df["year"] = df["created_at"].dt.year.astype("Int64")

    for c in ["assignee_name", "issue_type", "priority", "status", "sla_status", "key"]:
        if c in df.columns:
            df[c] = df[c].fillna("N/A")

    return df


def load_gold_backlog() -> pd.DataFrame:
    df = read_csv_or_empty(GOLD_BACKLOG_PATH)
    if df.empty:
        return df

    df["created_at"] = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True)
    df["as_of_ts"] = pd.to_datetime(df.get("as_of_ts"), errors="coerce", utc=True)

    df["age_business_hours"] = pd.to_numeric(df.get("age_business_hours"), errors="coerce").fillna(0.0)
    df["sla_target_hours"] = pd.to_numeric(df.get("sla_target_hours"), errors="coerce")

    if "is_overdue" in df.columns:
        df["is_overdue"] = df["is_overdue"].astype(bool)
    else:
        df["is_overdue"] = df["age_business_hours"] > df["sla_target_hours"]

    df["year"] = df["created_at"].dt.year.astype("Int64")

    for c in ["assignee_name", "issue_type", "priority", "status", "key"]:
        if c in df.columns:
            df[c] = df[c].fillna("N/A")

    return df


def load_silver_invalid() -> pd.DataFrame:
    df = read_csv_or_empty(SILVER_INVALID_PATH)
    if df.empty:
        return df

    for c in ["invalid_reason", "key", "issue_id", "row_status", "created_status", "resolved_status", "assignee_name"]:
        if c in df.columns:
            df[c] = df[c].fillna("N/A")
    return df


def load_silver_dq_metrics_df() -> pd.DataFrame:
    dq = read_json_or_empty(SILVER_DQ_JSON_PATH)
    if dq:
        total = int(dq.get("total", 0))
        valid = int(dq.get("valid", 0))
        missing = int(dq.get("missing", 0))
        invalid = int(dq.get("invalid", 0))

        invalid_pct = round((invalid / total) * 100, 2) if total else 0.0
        missing_pct = round((missing / total) * 100, 2) if total else 0.0
        valid_pct = round((valid / total) * 100, 2) if total else 0.0

        metrics = [
            ("total_records", total),
            ("valid_records", valid),
            ("missing_records", missing),
            ("invalid_records", invalid),
            ("valid_pct", valid_pct),
            ("missing_pct", missing_pct),
            ("invalid_pct", invalid_pct),
            ("created_at_missing_count", int(dq.get("created_missing", 0))),
            ("created_at_invalid_count", int(dq.get("created_invalid", 0))),
            ("resolved_missing_count", int(dq.get("resolved_missing", 0))),
            ("resolved_invalid_count", int(dq.get("resolved_invalid", 0))),
        ]
        return pd.DataFrame(metrics, columns=["metric", "value"])

    return read_csv_or_empty(SILVER_DQ_CSV_PATH)


def get_silver_dq_counts() -> tuple[int, int, int, int]:
    dq = read_json_or_empty(SILVER_DQ_JSON_PATH)
    if dq:
        return (
            int(dq.get("total", 0)),
            int(dq.get("valid", 0)),
            int(dq.get("missing", 0)),
            int(dq.get("invalid", 0)),
        )
    return 0, 0, 0, 0


def get_silver_dq_counts_for_pie() -> tuple[int, int, int]:
    _, v, m, i = get_silver_dq_counts()
    return v, m, i


def _options(df: pd.DataFrame, col: str) -> list[str]:
    if df.empty or col not in df.columns:
        return []
    return sorted(df[col].dropna().astype(str).unique().tolist())


# ================================
# Header
# ================================
top_left, top_right = st.columns([3, 1], vertical_alignment="center")
with top_left:
    st.title("SLA Monitoring Dashboard")
    st.caption("SLA monitoring dashboard: filterable insights powered by deterministic pipeline outputs (CSV).")
with top_right:
    if st.button("🔄 Reload data", width="stretch"):
        st.cache_data.clear()
        st.rerun()

st.divider()

# ================================
# Sidebar
# ================================
st.sidebar.markdown("### 🔎 Filters")
mask_people = st.sidebar.checkbox("🔒 Mask names (privacy)", value=False)

df_gold = load_gold_resolved()
df_backlog = load_gold_backlog()

if df_gold.empty and df_backlog.empty:
    st.warning(
        "No Gold datasets found.\n\n"
        "Make sure the pipeline generated:\n"
        f"- {GOLD_RESOLVED_PATH}\n"
        f"- {GOLD_BACKLOG_PATH}\n"
    )
    st.stop()

if mask_people:
    if not df_gold.empty and "assignee_name" in df_gold.columns:
        df_gold["assignee_name"] = df_gold["assignee_name"].astype(str).apply(mask_name)
    if not df_backlog.empty and "assignee_name" in df_backlog.columns:
        df_backlog["assignee_name"] = df_backlog["assignee_name"].astype(str).apply(mask_name)

if not df_gold.empty:
    year_options = sorted([int(x) for x in df_gold["year"].dropna().unique().tolist()])
else:
    year_options = sorted([int(x) for x in df_backlog["year"].dropna().unique().tolist()])

selected_years = st.sidebar.multiselect(
    "Year (created_at)",
    options=year_options,
    default=year_options if year_options else None,
)

priority_options = _options(df_gold if not df_gold.empty else df_backlog, "priority")
type_options = _options(df_gold if not df_gold.empty else df_backlog, "issue_type")
assignee_options = _options(df_gold if not df_gold.empty else df_backlog, "assignee_name")

selected_priority = st.sidebar.multiselect("Priority", options=priority_options, default=priority_options)
selected_type = st.sidebar.multiselect("Issue Type", options=type_options, default=type_options)
selected_assignee = st.sidebar.selectbox("Analyst", options=["All"] + assignee_options, index=0)

selected_sla_status: list[str] = []
sla_range = (0.0, 0.0)
start_date = date.today()
end_date = date.today()

df_view = df_gold.copy()
if not df_view.empty:
    sla_status_options = sorted(df_view["sla_status"].dropna().unique().tolist())
    default_sla_status = [s for s in ["BREACHED", "MET"] if s in sla_status_options] or sla_status_options
    selected_sla_status = st.sidebar.multiselect(
        "SLA Status (Resolved)",
        options=sla_status_options,
        default=default_sla_status,
    )

    min_h = float(df_view["resolution_hours"].min()) if len(df_view) else 0.0
    max_h = float(df_view["resolution_hours"].max()) if len(df_view) else 0.0
    min_h_r = float(round(min_h, 2))
    max_h_r = float(round(max_h, 2))

    if max_h_r > min_h_r:
        sla_range = st.sidebar.slider(
            "SLA Range (business hours) — Resolved",
            min_value=min_h_r,
            max_value=max_h_r,
            value=(min_h_r, max_h_r),
        )
    else:
        sla_range = (min_h_r, max_h_r)

    min_d = df_view["created_at"].min()
    max_d = df_view["created_at"].max()

    if not pd.isna(min_d) and not pd.isna(max_d):
        start_date = min_d.date()
        end_date = max_d.date()

    date_range = st.sidebar.date_input(
        "created_at Date Range — Resolved",
        value=(start_date, end_date),
        min_value=start_date,
        max_value=end_date,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range


# Apply filters (Resolved view)
if not df_view.empty:
    if selected_years:
        df_view = df_view[df_view["year"].isin(selected_years)]
    if selected_priority:
        df_view = df_view[df_view["priority"].isin(selected_priority)]
    if selected_type:
        df_view = df_view[df_view["issue_type"].isin(selected_type)]
    if selected_assignee != "All":
        df_view = df_view[df_view["assignee_name"] == selected_assignee]
    if selected_sla_status:
        df_view = df_view[df_view["sla_status"].isin(selected_sla_status)]
    if "resolution_hours" in df_view.columns:
        df_view = df_view[df_view["resolution_hours"].between(sla_range[0], sla_range[1])]
    df_view = df_view[df_view["created_at"].notna()].copy()
    df_view = df_view[
        (df_view["created_at"].dt.date >= start_date) & (df_view["created_at"].dt.date <= end_date)
    ].copy()


# ================================
# Tabs
# ================================
tab_deliv, tab_overview, tab_reports, tab_viol, tab_dq, tab_backlog, tab_gov = st.tabs(
    [
        "✅ Deliverables",
        "🧠 Overview",
        "📊 Reports",
        "🚨 SLA Breaches",
        "🧪 Data Quality",
        "⏳ Backlog",
        "🏛️ Governance",
    ]
)

# ================================
# TAB 0 - Deliverables
# ================================
with tab_deliv:
    st.subheader("Required Deliverables (CSV)")
    st.caption("Assessment-required outputs + a clear reconciliation across Bronze → Silver → Gold.")

    topA, topB = st.columns([2, 1], vertical_alignment="top")

    # ---- Pipeline last run (audit OPTIONAL, .gitignore-friendly) ----
    with topA:
        with st.container(border=True):
            st.markdown("#### Pipeline last run")

            if not AUDIT_RUN_LOG_PATH.exists():
                st.info(
                    "Audit file not found. This is expected when `data/audit/` is excluded by `.gitignore`.\n\n"
                    "Run the pipeline locally to generate:\n"
                    "- `data/audit/run_log.csv`\n"
                    "- `data/audit/lineage.json`"
                )
            else:
                run_log = read_csv_or_empty(AUDIT_RUN_LOG_PATH)
                last_run = get_last_run_summary(run_log) if not run_log.empty else {}

                if not last_run:
                    st.info(
                        "`data/audit/run_log.csv` exists but is empty.\n\n"
                        "Run the pipeline again to populate audit events."
                    )
                else:
                    status = last_run.get("status") or "N/A"
                    start_txt = _fmt_dt_utc(last_run.get("start"))
                    end_txt = _fmt_dt_utc(last_run.get("end"))
                    dur = last_run.get("duration_s")
                    dur_txt = f"{float(dur):.2f}s" if dur is not None and str(dur) != "nan" else "N/A"

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Status", status)
                    c2.metric("Started", start_txt.replace(" UTC", ""))
                    c3.metric("Duration", dur_txt)
                    st.caption("Source: data/audit/run_log.csv")

    with topB:
        with st.container(border=True):
            st.markdown("#### Download bundle")
            zip_bytes = build_required_zip_bytes()
            if zip_bytes is None:
                st.info("ZIP available only when all required CSVs exist.")
                st.download_button(
                    "⬇️ Download required_outputs.zip",
                    data=b"",
                    file_name="required_outputs.zip",
                    mime="application/zip",
                    disabled=True,
                    width="stretch",
                )
            else:
                st.download_button(
                    "⬇️ Download required_outputs.zip",
                    data=zip_bytes,
                    file_name="required_outputs.zip",
                    mime="application/zip",
                    width="stretch",
                )
                st.caption("Includes the 3 required Gold CSVs.")

    st.divider()

    # --------- Volume reconciliation ----------
    st.markdown("### Data Pipeline Volume (Bronze → Silver → Gold)")
    st.caption("Traceability of record counts across layers (ingested → validated → delivered).")

    bronze_cnt = count_bronze_records(BRONZE_RAW_PATH)
    silver_total, silver_valid, silver_missing, silver_invalid = get_silver_dq_counts()
    silver_usable = max(silver_total - silver_invalid, 0)  # valid + missing

    gold_resolved_cnt = int(len(read_csv_or_empty(GOLD_RESOLVED_PATH)))
    gold_backlog_cnt = int(len(read_csv_or_empty(GOLD_BACKLOG_PATH)))  # optional
    gold_total_cnt = gold_resolved_cnt + gold_backlog_cnt

    coverage_usable = round((gold_total_cnt / silver_usable) * 100, 2) if silver_usable else 0.0

    with st.container(border=True):
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Bronze ingested", bronze_cnt)
        k2.metric("Silver total", silver_total)
        k3.metric("Silver usable", silver_usable)
        k4.metric("Gold delivered", gold_total_cnt)

        st.markdown(
            f"""
<div class="kpi-note">
<b>Silver breakdown</b>: valid={silver_valid} • missing={silver_missing} • invalid={silver_invalid} <br/>
<b>Gold breakdown</b>: resolved={gold_resolved_cnt} • backlog={gold_backlog_cnt} • total={gold_total_cnt} <br/>
<b>Coverage</b> (Gold vs Silver usable): {coverage_usable:.2f}%
</div>
""",
            unsafe_allow_html=True,
        )

        if silver_usable and gold_total_cnt and silver_usable != gold_total_cnt:
            st.warning(
                f"Reconciliation mismatch: Silver usable={silver_usable} vs Gold delivered={gold_total_cnt}. "
                "This can happen if Gold applies additional filters or if a file is missing."
            )

    st.divider()

    # --------- Required files cards ----------
    req_files = [
        ("Gold SLA Issues (Resolved)", GOLD_RESOLVED_PATH, "gold_sla_issues.csv"),
        ("Gold SLA by Analyst", GOLD_BY_ANALYST_PATH, "gold_sla_by_analyst.csv"),
        ("Gold SLA by Issue Type", GOLD_BY_ISSUE_TYPE_PATH, "gold_sla_by_issue_type.csv"),
    ]

    g1, g2, g3 = st.columns(3, vertical_alignment="top")
    slots = [g1, g2, g3]

    for i, (title, path, fname) in enumerate(req_files):
        exists, _size, mtime = _file_info(path)
        rows = int(len(read_csv_or_empty(path))) if exists else 0
        updated_txt = mtime.strftime("%Y-%m-%d %H:%M:%S") if mtime else "N/A"

        with slots[i]:
            with st.container(border=True):
                st.markdown(f"#### {title}")
                st.caption(f"`{path.relative_to(BASE_DIR)}`")
                a, b = st.columns([1, 1])
                a.metric("Status", "FOUND ✅" if exists else "MISSING ❌")
                b.metric("Rows", rows)
                st.markdown(
                    f"""
<div class="small-strong">Updated</div>
<div class="small-muted">{updated_txt}</div>
""",
                    unsafe_allow_html=True,
                )
                render_download_button(path, "⬇️ Download CSV", fname)

    st.divider()

    st.markdown("#### Preview (required outputs)")
    with st.expander("Preview: gold_sla_issues.csv", expanded=False):
        render_df(read_csv_or_empty(GOLD_RESOLVED_PATH), preview_rows=200)

    with st.expander("Preview: gold_sla_by_analyst.csv", expanded=False):
        render_df(read_csv_or_empty(GOLD_BY_ANALYST_PATH), preview_rows=200)

    with st.expander("Preview: gold_sla_by_issue_type.csv", expanded=False):
        render_df(read_csv_or_empty(GOLD_BY_ISSUE_TYPE_PATH), preview_rows=200)


# ================================
# TAB 1 - Overview
# ================================
with tab_overview:
    st.subheader("Overview (Gold Resolved)")
    if df_gold.empty:
        st.info("Gold Resolved dataset is empty.")
        st.stop()
    if df_view.empty:
        st.warning("No records match the selected filters.")
        st.stop()

    total_issues = int(len(df_view))
    sla_met = int((df_view["sla_status"] == "MET").sum())
    sla_breached = int((df_view["sla_status"] == "BREACHED").sum())
    sla_compliance_pct = round((sla_met / total_issues) * 100, 2) if total_issues else 0.0

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Resolved Issues", total_issues)
        c2.metric("SLA Met", sla_met)
        c3.metric("SLA Breached", sla_breached)
        c4.metric("Compliance (%)", f"{sla_compliance_pct:.2f}%")

    st.divider()

    left, right = st.columns(2)
    with left:
        dist = (
            df_view.groupby("sla_status")["issue_id"]
            .count()
            .reset_index()
            .rename(columns={"issue_id": "count"})
        )
        fig = px.pie(dist, names="sla_status", values="count", title="SLA Status Distribution (Resolved)")
        st.plotly_chart(fig, width="stretch")

    with right:
        sla_by_priority = df_view.groupby("priority")["sla_met"].mean().reset_index()
        sla_by_priority["sla_met_pct"] = (sla_by_priority["sla_met"] * 100).round(2)
        fig = px.bar(
            sla_by_priority,
            x="priority",
            y="sla_met_pct",
            title="SLA Met (%) by Priority (Resolved)",
            text="sla_met_pct",
            template="plotly_white",
        )
        st.plotly_chart(fig, width="stretch")

    st.divider()

    st.markdown("#### SLA Issue Details (Resolved)")
    cols = [
        "issue_id",
        "key",
        "issue_type",
        "assignee_name",
        "priority",
        "status",
        "sla_status",
        "created_at",
        "resolved_at",
        "resolution_hours",
        "sla_expected_hours",
    ]
    show_cols = [c for c in cols if c in df_view.columns]
    render_df(df_view[show_cols].reset_index(drop=True), preview_rows=200)

    st.download_button(
        "⬇️ Download filtered Gold Resolved (CSV)",
        data=df_view[show_cols].to_csv(index=False).encode("utf-8"),
        file_name="gold_resolved_filtered.csv",
        mime="text/csv",
        width="stretch",
    )


# ================================
# TAB 2 - Reports
# ================================
with tab_reports:
    st.subheader("Reports (Gold Resolved)")
    if df_gold.empty or df_view.empty:
        st.info("No Resolved data for the current filters.")
        st.stop()

    st.markdown("#### SLA Met Ranking by Analyst")
    ranking = (
        df_view[df_view["sla_status"] == "MET"]
        .groupby("assignee_name")["issue_id"]
        .count()
        .reset_index()
        .rename(columns={"issue_id": "sla_met_count"})
        .sort_values("sla_met_count", ascending=False)
    )

    col_bar, col_pie = st.columns(2)
    with col_bar:
        if ranking.empty:
            st.info("No MET records for the current filters.")
        else:
            fig = px.bar(
                ranking.head(20),
                x="assignee_name",
                y="sla_met_count",
                title="SLA Met Ranking by Analyst (Top 20)",
                text="sla_met_count",
                template="plotly_white",
            )
            st.plotly_chart(fig, width="stretch")

    with col_pie:
        if ranking.empty:
            st.info("No MET records for the current filters.")
        else:
            pie_df = ranking.head(10).copy()
            others_count = int(ranking["sla_met_count"].iloc[10:].sum()) if len(ranking) > 10 else 0
            if others_count > 0:
                pie_df = pd.concat(
                    [pie_df, pd.DataFrame([{"assignee_name": "Others", "sla_met_count": others_count}])],
                    ignore_index=True,
                )
            fig = px.pie(pie_df, names="assignee_name", values="sla_met_count", title="SLA Met Share (Top 10)")
            st.plotly_chart(fig, width="stretch")

    st.divider()
    st.markdown("#### Summary tables (Filtered Gold Resolved)")
    col_a, col_b = st.columns(2)

    with col_a:
        rep_analyst = (
            df_view.groupby("assignee_name")
            .agg(qty_issues=("issue_id", "count"), avg_sla_hours=("resolution_hours", "mean"))
            .reset_index()
            .sort_values("avg_sla_hours")
        )
        rep_analyst["avg_sla_hours"] = rep_analyst["avg_sla_hours"].round(2)
        with st.container(border=True):
            st.caption("Average SLA (hours) by analyst")
            render_df(rep_analyst, preview_rows=200)

    with col_b:
        rep_type = (
            df_view.groupby("issue_type")
            .agg(qty_issues=("issue_id", "count"), avg_sla_hours=("resolution_hours", "mean"))
            .reset_index()
            .sort_values("avg_sla_hours")
        )
        rep_type["avg_sla_hours"] = rep_type["avg_sla_hours"].round(2)
        with st.container(border=True):
            st.caption("Average SLA (hours) by issue type")
            render_df(rep_type, preview_rows=200)


# ================================
# TAB 3 - Breaches (ALL RED)
# ================================
with tab_viol:
    st.subheader("SLA Breaches (Resolved only)")
    st.caption("Breach-only KPIs + risk by analyst + longest breaches (based on current filters).")

    if df_gold.empty or df_view.empty:
        st.info("No Resolved data for the current filters.")
        st.stop()

    breached = df_view[df_view["sla_status"] == "BREACHED"].copy()
    total_resolved = int(len(df_view))
    total_breached = int(len(breached))
    breach_rate = round((total_breached / total_resolved) * 100, 2) if total_resolved else 0.0

    breached["resolution_hours"] = pd.to_numeric(breached.get("resolution_hours"), errors="coerce").fillna(0.0)

    if breached.empty:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Breaches", 0)
            c2.metric("Breach rate (%)", f"{breach_rate:.2f}%")
            c3.metric("Avg breach (h)", "0.00")
            c4.metric("Max breach (h)", "0.00")
        st.success("No SLA breaches for the current filters ✅")
        st.stop()

    analyst_totals = (
        df_view.groupby("assignee_name")["issue_id"]
        .count()
        .reset_index()
        .rename(columns={"issue_id": "total_resolved"})
    )
    analyst_breached = (
        breached.groupby("assignee_name")["issue_id"]
        .count()
        .reset_index()
        .rename(columns={"issue_id": "sla_breached_count"})
    )

    risk = analyst_totals.merge(analyst_breached, on="assignee_name", how="left")
    risk["sla_breached_count"] = risk["sla_breached_count"].fillna(0).astype(int)
    risk["breach_pct"] = (risk["sla_breached_count"] / risk["total_resolved"]) * 100
    risk["breach_pct"] = risk["breach_pct"].fillna(0.0)

    risk_sorted = risk.sort_values(
        ["breach_pct", "sla_breached_count", "total_resolved"],
        ascending=[False, False, False],
    )
    top_risk = risk_sorted.iloc[0]
    top_risk_name = str(top_risk["assignee_name"])
    top_risk_pct = float(top_risk["breach_pct"])
    top_risk_breaches = int(top_risk["sla_breached_count"])
    top_risk_total = int(top_risk["total_resolved"])

    avg_breach_hours = float(breached["resolution_hours"].mean()) if len(breached) else 0.0
    max_breach_hours = float(breached["resolution_hours"].max()) if len(breached) else 0.0

    with st.container(border=True):
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Breaches", total_breached)
        c2.metric("Breach rate (%)", f"{breach_rate:.2f}%")
        c3.metric("Top risk analyst", top_risk_name)
        c4.metric("Breaches / Total", f"{top_risk_breaches} / {top_risk_total}")
        c5.metric("Avg breach (h)", f"{avg_breach_hours:.2f}")
        c6.metric("Max breach (h)", f"{max_breach_hours:.2f}")

    st.caption(f"Top risk analyst breach rate: {top_risk_pct:.2f}% (within current filters).")

    st.divider()

    st.markdown("### Breach Risk by Analyst")

    colA, colB = st.columns(2)
    with colA:
        vol = risk.sort_values("sla_breached_count", ascending=False).head(20).copy()
        fig = px.bar(
            vol,
            x="assignee_name",
            y="sla_breached_count",
            text="sla_breached_count",
            title="Breach Volume by Analyst (Top 20)",
            template="plotly_white",
            color_discrete_sequence=["#EF553B"],
        )
        st.plotly_chart(fig, width="stretch")

    with colB:
        pie_df = (
            risk.sort_values("sla_breached_count", ascending=False)[["assignee_name", "sla_breached_count"]]
            .head(10)
            .copy()
        )
        others_count = (
            int(risk.sort_values("sla_breached_count", ascending=False)["sla_breached_count"].iloc[10:].sum())
            if len(risk) > 10
            else 0
        )
        if others_count > 0:
            pie_df = pd.concat(
                [pie_df, pd.DataFrame([{"assignee_name": "Others", "sla_breached_count": others_count}])],
                ignore_index=True,
            )
        fig = px.pie(
            pie_df,
            names="assignee_name",
            values="sla_breached_count",
            title="Breach Share by Analyst (Top 10)",
            color_discrete_sequence=["#EF553B"],
        )
        st.plotly_chart(fig, width="stretch")

    st.markdown("### Breach Rate by Analyst (%)")
    rate = risk.sort_values(["breach_pct", "sla_breached_count"], ascending=[False, False]).head(20).copy()
    rate["breach_pct_label"] = rate["breach_pct"].round(2)

    fig = px.bar(
        rate,
        x="assignee_name",
        y="breach_pct",
        text="breach_pct_label",
        title="Breach Rate (Top 20) — % of resolved that breached",
        template="plotly_white",
        color_discrete_sequence=["#EF553B"],
    )
    fig.update_yaxes(ticksuffix="%")
    st.plotly_chart(fig, width="stretch")

    st.divider()

    st.download_button(
        "⬇️ Download breaches (CSV)",
        data=breached.to_csv(index=False).encode("utf-8"),
        file_name="sla_breaches_breached.csv",
        mime="text/csv",
        width="stretch",
    )

    st.markdown("### Tickets that Breached SLA (Longest First)")
    breached_sorted = breached.sort_values("resolution_hours", ascending=False)

    cols = [
        "issue_id",
        "key",
        "priority",
        "issue_type",
        "status",
        "assignee_name",
        "resolution_hours",
        "sla_expected_hours",
        "sla_status",
        "created_at",
        "resolved_at",
    ]
    show_cols = [c for c in cols if c in breached_sorted.columns]

    st.markdown("#### Top 30 longest breaches")
    render_df(breached_sorted[show_cols].head(30).reset_index(drop=True), preview_rows=30)

    st.divider()

    st.markdown("### Longest Breaches Analysis")
    col1, col2 = st.columns(2)

    with col1:
        label_col = "key" if "key" in breached_sorted.columns else "issue_id"
        topN = breached_sorted.head(20).copy()
        fig = px.bar(
            topN,
            x=label_col,
            y="resolution_hours",
            text=topN["resolution_hours"].round(2),
            title="Top 20 longest breached tickets (resolution_hours)",
            template="plotly_white",
            color_discrete_sequence=["#EF553B"],
        )
        st.plotly_chart(fig, width="stretch")

    with col2:
        fig = px.histogram(
            breached_sorted,
            x="resolution_hours",
            nbins=30,
            title="Resolution time distribution (breached only)",
            template="plotly_white",
            color_discrete_sequence=["#EF553B"],
        )
        st.plotly_chart(fig, width="stretch")

    st.divider()

    st.markdown("### Avg breach resolution (hours) by analyst")
    if "assignee_name" in breached_sorted.columns:
        avg_by_analyst = (
            breached_sorted.groupby("assignee_name")["resolution_hours"]
            .mean()
            .reset_index()
            .rename(columns={"resolution_hours": "avg_breach_hours"})
            .sort_values("avg_breach_hours", ascending=False)
            .head(20)
        )
        fig = px.bar(
            avg_by_analyst,
            x="assignee_name",
            y="avg_breach_hours",
            text=avg_by_analyst["avg_breach_hours"].round(2),
            title="Average breach resolution time by analyst (Top 20)",
            template="plotly_white",
            color_discrete_sequence=["#EF553B"],
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Column 'assignee_name' not found in breached dataset.")

    st.divider()
    st.markdown("### All breached records (preview)")
    render_df(breached_sorted[show_cols].reset_index(drop=True), preview_rows=200)


# ================================
# TAB 4 - Data Quality
# ================================
with tab_dq:
    st.subheader("Data Quality (Silver)")

    dq_metrics_df = load_silver_dq_metrics_df()
    invalid_df = load_silver_invalid()

    if mask_people and not invalid_df.empty and "assignee_name" in invalid_df.columns:
        invalid_df["assignee_name"] = invalid_df["assignee_name"].astype(str).apply(mask_name)

    col1, col2 = st.columns([1, 2])
    with col1:
        with st.container(border=True):
            st.markdown("#### DQ metrics")
            if dq_metrics_df.empty:
                st.info("silver_dq.json not found or empty.")
            else:
                render_df(dq_metrics_df, preview_rows=200)

        v, m, i = get_silver_dq_counts_for_pie()
        pie_counts = pd.DataFrame(
            [{"status": "VALID", "count": v}, {"status": "MISSING", "count": m}, {"status": "INVALID", "count": i}]
        )
        if int(pie_counts["count"].sum()) > 0:
            fig = px.pie(pie_counts, names="status", values="count", title="DQ Distribution")
            st.plotly_chart(fig, width="stretch")

    with col2:
        with st.container(border=True):
            st.markdown("#### Invalid records (Silver)")
            if invalid_df.empty:
                st.success("No invalid records found ✅")
            else:
                st.download_button(
                    "⬇️ Download invalid records (CSV)",
                    data=invalid_df.to_csv(index=False).encode("utf-8"),
                    file_name="silver_invalid_records.csv",
                    mime="text/csv",
                    width="stretch",
                )
                render_df(invalid_df.reset_index(drop=True), preview_rows=200)


# ================================
# TAB 5 - Backlog
# ================================
with tab_backlog:
    st.subheader("SLA Backlog (Gold Backlog)")
    if df_backlog.empty:
        st.info("Gold Backlog dataset is empty.")
        st.stop()

    df_b = df_backlog.copy()
    if selected_years:
        df_b = df_b[df_b["year"].isin(selected_years)]
    if selected_priority:
        df_b = df_b[df_b["priority"].isin(selected_priority)]
    if selected_type:
        df_b = df_b[df_b["issue_type"].isin(selected_type)]
    if selected_assignee != "All":
        df_b = df_b[df_b["assignee_name"] == selected_assignee]

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("Backlog issues", int(len(df_b)))
        c2.metric("Overdue", int(df_b["is_overdue"].sum()) if "is_overdue" in df_b.columns else 0)
        overdue_rate = (
            (float(df_b["is_overdue"].mean() * 100) if len(df_b) else 0.0)
            if "is_overdue" in df_b.columns
            else 0.0
        )
        c3.metric("Overdue rate (%)", f"{overdue_rate:.2f}%")

    st.divider()

    show_cols = [
        c
        for c in [
            "issue_id",
            "key",
            "issue_type",
            "assignee_name",
            "priority",
            "status",
            "created_at",
            "as_of_ts",
            "age_business_hours",
            "sla_target_hours",
            "is_overdue",
        ]
        if c in df_b.columns
    ]

    render_df(df_b[show_cols].reset_index(drop=True), preview_rows=200)
    st.download_button(
        "⬇️ Download filtered Backlog (CSV)",
        data=df_b[show_cols].to_csv(index=False).encode("utf-8"),
        file_name="gold_backlog_filtered.csv",
        mime="text/csv",
        width="stretch",
    )


# ================================
# TAB 6 - Governance
# ================================
with tab_gov:
    st.subheader("Governance")

    colA, colB = st.columns(2)
    with colA:
        with st.container(border=True):
            st.markdown("#### Run Log (audit)")
            if not AUDIT_RUN_LOG_PATH.exists():
                st.info(
                    "Audit file not found. This is expected when `data/audit/` is excluded by `.gitignore`.\n\n"
                    "Run the pipeline locally to generate `data/audit/run_log.csv`."
                )
            else:
                run_log = read_csv_or_empty(AUDIT_RUN_LOG_PATH)
                if run_log.empty:
                    st.info("data/audit/run_log.csv exists but is empty.")
                else:
                    render_df(run_log, preview_rows=200)
                    st.download_button(
                        "⬇️ Download run_log.csv",
                        data=run_log.to_csv(index=False).encode("utf-8"),
                        file_name="run_log.csv",
                        mime="text/csv",
                        width="stretch",
                    )

    with colB:
        with st.container(border=True):
            st.markdown("#### Lineage")
            lineage = read_json_or_empty(AUDIT_LINEAGE_PATH)
            if not lineage:
                st.info(
                    "data/audit/lineage.json not found. This is expected when `data/audit/` is excluded by `.gitignore`.\n\n"
                    "Run the pipeline locally to generate `data/audit/lineage.json`."
                )
            else:
                st.json(lineage)

    st.divider()
    with st.container(border=True):
        st.markdown("#### Current DQ snapshot (silver_dq.json)")
        dq_now = read_json_or_empty(SILVER_DQ_JSON_PATH)
        if dq_now:
            st.json(dq_now)
        else:
            st.info("silver_dq.json not found.")