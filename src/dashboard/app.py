"""
SLA Dashboard (CSV-only)

SILVER:
  - data/silver/silver_issues_invalid.csv
  - data/silver/silver_dq.json   (canonical DQ output)

GOLD (CSV):
  - data/gold/gold_sla_issues.csv          (resolved issues)
  - data/gold/gold_sla_backlog.csv         (open issues / in-progress)

AUDIT / GOVERNANCE (optional):
  - data/audit/run_log.csv
  - data/audit/lineage.json

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

from datetime import date
import json

import plotly.io as pio

# Global default (still force template per chart below for consistency)
pio.templates.default = "plotly_dark"

import pandas as pd
import streamlit as st
import plotly.express as px

from src.common.logger import get_logger
from src.common.governance import mask_name


logger = get_logger(__name__, layer="DASHBOARD")

# ================================
# Paths
# ================================
BASE_DIR = Path(__file__).resolve().parents[2]

GOLD_RESOLVED_PATH = BASE_DIR / "data" / "gold" / "gold_sla_issues.csv"
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

st.title("📈 SLA Monitoring Dashboard")
st.caption("Python-based Medallion pipeline outputs (CSV) — Gold Resolved + Gold Backlog")


# ================================
# Helpers
# ================================
def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame into UTF-8 CSV bytes (for Streamlit download buttons)."""
    return df.to_csv(index=False).encode("utf-8")


@st.cache_data(show_spinner=False)
def read_csv_or_empty(path: Path) -> pd.DataFrame:
    """Read a CSV file safely; returns an empty DataFrame if missing or empty."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


@st.cache_data(show_spinner=False)
def read_json_or_empty(path: Path) -> dict:
    """Read a JSON file safely; returns an empty dict if missing/empty/invalid."""
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to read JSON: {path} | {e}")
        return {}


def load_gold_resolved() -> pd.DataFrame:
    """Load Gold resolved dataset and normalize columns for dashboard usage."""
    df = read_csv_or_empty(GOLD_RESOLVED_PATH)
    if df.empty:
        return df

    # Normalize timestamps
    df["created_at"] = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True)
    df["resolved_at"] = pd.to_datetime(df.get("resolved_at"), errors="coerce", utc=True)

    # Normalize resolution hours column
    if "resolution_hours" not in df.columns and "resolution_business_hours" in df.columns:
        df["resolution_hours"] = pd.to_numeric(df["resolution_business_hours"], errors="coerce")

    if "resolution_hours" not in df.columns:
        df["resolution_hours"] = 0.0
    else:
        df["resolution_hours"] = pd.to_numeric(df["resolution_hours"], errors="coerce").fillna(0.0)

    # Normalize SLA expected hours
    if "sla_expected_hours" not in df.columns:
        df["sla_expected_hours"] = pd.NA
    else:
        df["sla_expected_hours"] = pd.to_numeric(df["sla_expected_hours"], errors="coerce")

    # Normalize SLA status
    if "is_sla_met" in df.columns:
        df["sla_met"] = df["is_sla_met"].astype(bool).astype(int)
        df["sla_status"] = df["is_sla_met"].apply(lambda x: "MET" if bool(x) else "BREACHED")
    else:
        df["sla_status"] = df.get("sla_status", "NOT_CALCULATED").fillna("NOT_CALCULATED")
        df["sla_met"] = (df["sla_status"] == "MET").astype(int)

    # Year used by sidebar filters
    df["year"] = df["created_at"].dt.year.astype("Int64")

    # Fill common dimensions
    for c in ["assignee_name", "issue_type", "priority", "status", "sla_status", "key"]:
        if c in df.columns:
            df[c] = df[c].fillna("N/A")

    return df


def load_gold_backlog() -> pd.DataFrame:
    """Load Gold backlog dataset and normalize columns for dashboard usage."""
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
    """Load Silver invalid dataset and normalize display columns."""
    df = read_csv_or_empty(SILVER_INVALID_PATH)
    if df.empty:
        return df

    for c in ["invalid_reason", "key", "issue_id", "row_status", "created_status", "resolved_status", "assignee_name"]:
        if c in df.columns:
            df[c] = df[c].fillna("N/A")
    return df


def load_silver_dq_metrics_df() -> pd.DataFrame:
    """
    Canonical: reads data/silver/silver_dq.json and converts it into a 2-col table (metric/value).
    Fallback: reads silver_dq_report.csv if JSON is missing.
    """
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

    # Optional fallback if your project still has this file
    return read_csv_or_empty(SILVER_DQ_CSV_PATH)


def get_silver_dq_counts_for_pie() -> tuple[int, int, int]:
    """Return (valid, missing, invalid) counts for DQ pie chart."""
    dq = read_json_or_empty(SILVER_DQ_JSON_PATH)
    if dq:
        return int(dq.get("valid", 0)), int(dq.get("missing", 0)), int(dq.get("invalid", 0))
    return 0, 0, 0


def _options(df: pd.DataFrame, col: str) -> list[str]:
    """Return sorted unique string options for a column (safe)."""
    if df.empty or col not in df.columns:
        return []
    return sorted(df[col].dropna().astype(str).unique().tolist())


def _apply_dark_layout(fig, title_center: bool = True):
    """Force Plotly dark theme per figure (prevents Streamlit theme overrides)."""
    fig.update_layout(
        template="plotly_dark",
        font=dict(size=14),
        margin=dict(l=40, r=40, t=70, b=40),
    )
    if title_center:
        fig.update_layout(title=dict(x=0.5, xanchor="center"))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.10)")
    return fig


# ================================
# Sidebar
# ================================
st.sidebar.markdown("### 🔎 Filters")

if st.sidebar.button("🔄 Reload data"):
    st.cache_data.clear()
    st.rerun()

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

# Apply name masking (dashboard-only)
if mask_people:
    if not df_gold.empty and "assignee_name" in df_gold.columns:
        df_gold["assignee_name"] = df_gold["assignee_name"].astype(str).apply(mask_name)
    if not df_backlog.empty and "assignee_name" in df_backlog.columns:
        df_backlog["assignee_name"] = df_backlog["assignee_name"].astype(str).apply(mask_name)

# Sidebar options
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

# Resolved-only filters (initialize defaults safely)
selected_sla_status: list[str] = []
sla_range = (0.0, 0.0)
start_date = date.today()
end_date = date.today()

if not df_gold.empty:
    sla_status_options = sorted(df_gold["sla_status"].dropna().unique().tolist())
    default_sla_status = [s for s in ["BREACHED", "MET"] if s in sla_status_options] or sla_status_options
    selected_sla_status = st.sidebar.multiselect(
        "SLA Status (Resolved)",
        options=sla_status_options,
        default=default_sla_status,
    )

    min_h = float(df_gold["resolution_hours"].min()) if len(df_gold) else 0.0
    max_h = float(df_gold["resolution_hours"].max()) if len(df_gold) else 0.0
    min_h_r = float(round(min_h, 2))
    max_h_r = float(round(max_h, 2))

    # Streamlit slider requires min_value < max_value; otherwise keep filter disabled.
    if max_h_r <= min_h_r:
        sla_range = (min_h_r, max_h_r)
        st.sidebar.caption("SLA Range (business hours): all values are equal — filter disabled.")
    else:
        sla_range = st.sidebar.slider(
            "SLA Range (business hours) — Resolved",
            min_value=min_h_r,
            max_value=max_h_r,
            value=(min_h_r, max_h_r),
        )

    min_d = df_gold["created_at"].min()
    max_d = df_gold["created_at"].max()

    if pd.isna(min_d) or pd.isna(max_d):
        start_date = date.today()
        end_date = date.today()
    else:
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
    else:
        start_date, end_date = start_date, end_date


# ================================
# Apply filters (Resolved view)
# ================================
df_view = df_gold.copy()

if not df_view.empty:
    if selected_years:
        df_view = df_view[df_view["year"].isin(selected_years)]
    if selected_priority:
        df_view = df_view[df_view["priority"].isin(selected_priority)]
    if selected_type:
        df_view = df_view[df_view["issue_type"].isin(selected_type)]
    if selected_assignee != "All":
        df_view = df_view[df_view["assignee_name"] == selected_assignee]
    if "sla_status" in df_view.columns and selected_sla_status:
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
tab_overview, tab_reports, tab_viol, tab_dq, tab_backlog, tab_gov = st.tabs(
    [
        "🧠 Overview (Gold Resolved)",
        "📊 Reports (Gold Resolved)",
        "🚨 SLA Breaches",
        "🧪 Data Quality (Silver)",
        "⏳ SLA Backlog (Gold Backlog)",
        "🏛️ Governance",
    ]
)

# ================================
# TAB 1 - Overview (Resolved)
# ================================
with tab_overview:
    st.subheader("🧠 Overview (Gold Resolved)")

    if df_gold.empty:
        st.info("Gold Resolved dataset is empty. Check the Backlog tab for in-progress SLA.")
        st.stop()

    if df_view.empty:
        st.warning("No records match the selected filters.")
        st.stop()

    total_issues = int(len(df_view))
    sla_met = int((df_view["sla_status"] == "MET").sum())
    sla_breached = int((df_view["sla_status"] == "BREACHED").sum())
    sla_compliance_pct = round((sla_met / total_issues) * 100, 2) if total_issues else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📦 Total Issues (Resolved)", total_issues)
    c2.metric("✅ SLA Met", sla_met)
    c3.metric("❌ SLA Breached", sla_breached)
    c4.metric("🎯 SLA Compliance (%)", f"{sla_compliance_pct:.2f}%")

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
        _apply_dark_layout(fig)
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
            template="plotly_dark",
        )
        _apply_dark_layout(fig)
        st.plotly_chart(fig, width="stretch")

    st.divider()

    prio_dist = (
        df_view["priority"]
        .value_counts(dropna=False)
        .reset_index()
        .rename(columns={"index": "priority", "priority": "count"})
    )
    prio_dist.columns = ["priority", "count"]

    fig = px.pie(
        prio_dist,
        names="priority",
        values="count",
        title="📦 Issue Distribution by Priority (% of total)",
    )
    _apply_dark_layout(fig)
    st.plotly_chart(fig, width="stretch")

    st.divider()

    st.markdown("### 🗂️ SLA Issue Details (Resolved)")
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
    st.dataframe(df_view[show_cols].reset_index(drop=True), width="stretch")

    st.download_button(
        "⬇️ Download filtered Gold Resolved (CSV)",
        data=to_csv_bytes(df_view[show_cols]),
        file_name="gold_resolved_filtered.csv",
        mime="text/csv",
    )


# ================================
# TAB 2 - Reports (Resolved)
# ================================
with tab_reports:
    st.subheader("📊 Reports (Gold Resolved)")

    if df_gold.empty or df_view.empty:
        st.info("No Resolved data for the current filters.")
        st.stop()

    st.markdown("### 🏆 SLA Met Ranking by Analyst")

    ranking = (
        df_view[df_view["sla_status"] == "MET"]
        .groupby("assignee_name")["issue_id"]
        .count()
        .reset_index()
        .rename(columns={"issue_id": "sla_met_count"})
        .sort_values("sla_met_count", ascending=False)
    )

    if ranking.empty:
        st.info("No MET records for the current filters.")
    else:
        col_bar, col_pie = st.columns(2)

        with col_bar:
            fig = px.bar(
                ranking.head(20),
                x="assignee_name",
                y="sla_met_count",
                title="SLA Met Ranking by Analyst (Top 20)",
                text="sla_met_count",
                template="plotly_dark",
            )
            _apply_dark_layout(fig)
            st.plotly_chart(fig, width="stretch")

        with col_pie:
            pie_df = ranking.head(10).copy()
            others_count = int(ranking["sla_met_count"].iloc[10:].sum()) if len(ranking) > 10 else 0
            if others_count > 0:
                pie_df = pd.concat(
                    [pie_df, pd.DataFrame([{"assignee_name": "Others", "sla_met_count": others_count}])],
                    ignore_index=True,
                )

            fig = px.pie(
                pie_df,
                names="assignee_name",
                values="sla_met_count",
                title="SLA Met Share by Analyst (Top 10)",
            )
            _apply_dark_layout(fig)
            st.plotly_chart(fig, width="stretch")

    st.divider()
    st.markdown("### 📋 Summary tables (Filtered Gold Resolved)")

    col_a, col_b = st.columns(2)

    with col_a:
        rep_analyst = (
            df_view.groupby("assignee_name")
            .agg(qty_issues=("issue_id", "count"), avg_sla_hours=("resolution_hours", "mean"))
            .reset_index()
            .sort_values("avg_sla_hours")
        )
        rep_analyst["avg_sla_hours"] = rep_analyst["avg_sla_hours"].round(2)
        st.caption("Average SLA (hours) by analyst")
        st.dataframe(rep_analyst, width="stretch")

    with col_b:
        rep_type = (
            df_view.groupby("issue_type")
            .agg(qty_issues=("issue_id", "count"), avg_sla_hours=("resolution_hours", "mean"))
            .reset_index()
            .sort_values("avg_sla_hours")
        )
        rep_type["avg_sla_hours"] = rep_type["avg_sla_hours"].round(2)
        st.caption("Average SLA (hours) by issue type")
        st.dataframe(rep_type, width="stretch")


# ================================
# TAB 3 - Breached (Resolved)
# ================================
with tab_viol:
    st.subheader("🚨 SLA Breaches (BREACHED)")
    st.caption("Breach-only KPIs, analyst risk, and longest-breach analysis (resolved issues only).")

    if df_gold.empty or df_view.empty:
        st.info("No Resolved data for the current filters.")
        st.stop()

    breached = df_view[df_view["sla_status"] == "BREACHED"].copy()

    total_resolved = int(len(df_view))
    total_breached = int(len(breached))
    breach_rate = round((total_breached / total_resolved) * 100, 2) if total_resolved else 0.0

    breached["resolution_hours"] = pd.to_numeric(breached.get("resolution_hours"), errors="coerce").fillna(0.0)

    if breached.empty:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("❌ Total Breaches", 0)
        c2.metric("📉 Breach Rate (%)", f"{breach_rate:.2f}%")
        c3.metric("🔥 Top Risk Analyst", "N/A")
        c4.metric("⏱️ Avg Breach (h)", "0.00")
        c5.metric("🚩 Max Breach (h)", "0.00")
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

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("❌ Total Breaches", total_breached)
    c2.metric("📉 Breach Rate (%)", f"{breach_rate:.2f}%")
    c3.metric("🔥 Top Risk Analyst", top_risk_name)
    c4.metric("📌 Breaches / Total", f"{top_risk_breaches} / {top_risk_total}")
    c5.metric("⏱️ Avg Breach (h)", f"{avg_breach_hours:.2f}")
    c6.metric("🚩 Max Breach (h)", f"{max_breach_hours:.2f}")

    st.caption(f"Top risk analyst breach rate: {top_risk_pct:.2f}% (based on current filters).")

    st.divider()

    st.markdown("## 🧯 Breach Risk by Analyst")

    colA, colB = st.columns(2)

    with colA:
        st.markdown("### Breach Volume by Analyst (Top 20)")
        vol = risk.sort_values("sla_breached_count", ascending=False).head(20)
        fig = px.bar(
            vol,
            x="assignee_name",
            y="sla_breached_count",
            text="sla_breached_count",
            title="Breach Volume (Top 20)",
            color_discrete_sequence=["#EF553B"],
            template="plotly_dark",
        )
        _apply_dark_layout(fig)
        st.plotly_chart(fig, width="stretch")

    with colB:
        st.markdown("### Breach Share by Analyst (Top 10)")
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
            title="Breach Share (Top 10)",
        )
        _apply_dark_layout(fig)
        st.plotly_chart(fig, width="stretch")

    st.markdown("## 📌 Breach Rate by Analyst (%)")

    rate = risk.sort_values(["breach_pct", "sla_breached_count"], ascending=[False, False]).head(20).copy()
    rate["breach_pct_label"] = rate["breach_pct"].round(2)

    fig = px.bar(
        rate,
        x="assignee_name",
        y="breach_pct",
        text="breach_pct_label",
        title="Breach Rate (Top 20) — % of resolved that breached",
        color_discrete_sequence=["#EF553B"],
        template="plotly_dark",
    )
    fig.update_yaxes(ticksuffix="%")
    _apply_dark_layout(fig)
    st.plotly_chart(fig, width="stretch")

    st.divider()

    st.download_button(
        "⬇️ Download breaches (CSV)",
        data=to_csv_bytes(breached),
        file_name="sla_breaches_breached.csv",
        mime="text/csv",
    )

    st.markdown("## 🔥 Tickets that Breached SLA (Longest First)")
    breached = breached.sort_values("resolution_hours", ascending=False)

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
    show_cols = [c for c in cols if c in breached.columns]

    top = breached.head(30)
    st.markdown("### Top 30 longest breaches")
    st.dataframe(top[show_cols].reset_index(drop=True), width="stretch")

    st.divider()
    st.markdown("## 📈 Longest Breaches Analysis")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Top 20 breaches by resolution time")
        label_col = "key" if "key" in breached.columns else "issue_id"
        topN = breached.head(20).copy()
        fig = px.bar(
            topN,
            x=label_col,
            y="resolution_hours",
            text=topN["resolution_hours"].round(2),
            title="Top 20 longest breached tickets (resolution_hours)",
            color_discrete_sequence=["#EF553B"],
            template="plotly_dark",
        )
        _apply_dark_layout(fig)
        st.plotly_chart(fig, width="stretch")

    with col2:
        st.markdown("### Resolution time distribution (breached only)")
        fig = px.histogram(
            breached,
            x="resolution_hours",
            nbins=30,
            title="Histogram — resolution_hours (BREACHED)",
            color_discrete_sequence=["#EF553B"],
            template="plotly_dark",
        )
        _apply_dark_layout(fig)
        st.plotly_chart(fig, width="stretch")

    st.divider()

    st.markdown("### Avg breach resolution (hours) by analyst")
    if "assignee_name" in breached.columns:
        avg_by_analyst = (
            breached.groupby("assignee_name")["resolution_hours"]
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
            color_discrete_sequence=["#EF553B"],
            template="plotly_dark",
        )
        _apply_dark_layout(fig)
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Column 'assignee_name' not found in breached dataset.")

    st.divider()
    st.markdown("### 📌 All breached records")
    st.dataframe(breached[show_cols].reset_index(drop=True), width="stretch")


# ================================
# TAB 4 - Silver DQ
# ================================
with tab_dq:
    st.subheader("🧪 Data Quality (Silver)")
    st.caption("Silver invalid records + DQ metrics")

    dq_metrics_df = load_silver_dq_metrics_df()
    invalid_df = load_silver_invalid()

    if mask_people and not invalid_df.empty and "assignee_name" in invalid_df.columns:
        invalid_df["assignee_name"] = invalid_df["assignee_name"].astype(str).apply(mask_name)

    col1, col2 = st.columns([1, 2])

    with col1:
        st.markdown("### 📏 DQ metrics")
        if dq_metrics_df.empty:
            st.info("silver_dq.json was not found (or is empty). Run the Silver normalization step.")
        else:
            st.dataframe(dq_metrics_df, width="stretch")

        v, m, i = get_silver_dq_counts_for_pie()
        pie_counts = pd.DataFrame(
            [
                {"status": "VALID", "count": v},
                {"status": "MISSING", "count": m},
                {"status": "INVALID", "count": i},
            ]
        )
        if int(pie_counts["count"].sum()) > 0:
            fig = px.pie(
                pie_counts,
                names="status",
                values="count",
                title="DQ Distribution (Valid / Missing / Invalid)",
            )
            _apply_dark_layout(fig)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No DQ data available for the pie chart (all counts are zero).")

    with col2:
        st.markdown("### 📄 Invalid records (Silver)")
        if invalid_df.empty:
            st.success("No invalid records found ✅")
        else:
            st.download_button(
                "⬇️ Download invalid records (CSV)",
                data=to_csv_bytes(invalid_df),
                file_name="silver_invalid_records.csv",
                mime="text/csv",
            )
            st.dataframe(invalid_df.reset_index(drop=True), width="stretch")


# ================================
# TAB 5 - Backlog SLA
# ================================
with tab_backlog:
    st.subheader("⏳ SLA Backlog (Gold Backlog)")
    st.caption("SLA age in business hours for open issues")

    if df_backlog.empty:
        st.info("Gold Backlog dataset is empty. Run the pipeline to generate the backlog output.")
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

    c1, c2, c3 = st.columns(3)
    c1.metric("📦 Backlog Issues", int(len(df_b)))
    c2.metric("⚠️ Overdue", int(df_b["is_overdue"].sum()) if "is_overdue" in df_b.columns else 0)
    overdue_rate = (
        (float(df_b["is_overdue"].mean() * 100) if len(df_b) else 0.0) if "is_overdue" in df_b.columns else 0.0
    )
    c3.metric("📌 Overdue Rate (%)", f"{overdue_rate:.2f}%")

    st.divider()

    if "is_overdue" in df_b.columns:
        dist = (
            df_b.groupby("is_overdue")["issue_id"]
            .count()
            .reset_index()
            .rename(columns={"issue_id": "count"})
        )
        dist["is_overdue"] = dist["is_overdue"].apply(lambda x: "OVERDUE" if bool(x) else "ON TRACK")
        fig = px.pie(dist, names="is_overdue", values="count", title="Backlog Overdue Distribution")
        _apply_dark_layout(fig)
        st.plotly_chart(fig, width="stretch")

    st.divider()
    st.markdown("### 📄 Backlog details")

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

    st.dataframe(df_b[show_cols].reset_index(drop=True), width="stretch")
    st.download_button(
        "⬇️ Download filtered Backlog (CSV)",
        data=to_csv_bytes(df_b[show_cols]),
        file_name="gold_backlog_filtered.csv",
        mime="text/csv",
    )


# ================================
# TAB 6 - Governance
# ================================
with tab_gov:
    st.subheader("🏛️ Governance")
    st.caption("Evidence: run log + lineage + DQ snapshot")

    colA, colB = st.columns(2)

    with colA:
        st.markdown("### 🧾 Run Log (audit)")
        run_log = read_csv_or_empty(AUDIT_RUN_LOG_PATH)
        if run_log.empty:
            st.info("data/audit/run_log.csv not found. Run the pipeline first.")
        else:
            if "start_ts" in run_log.columns:
                st.dataframe(run_log.sort_values("start_ts", ascending=False), width="stretch")
            else:
                st.dataframe(run_log, width="stretch")

            st.download_button(
                "⬇️ Download run_log.csv",
                data=to_csv_bytes(run_log),
                file_name="run_log.csv",
                mime="text/csv",
            )

    with colB:
        st.markdown("### 🧬 Lineage")
        lineage = read_json_or_empty(AUDIT_LINEAGE_PATH)
        if not lineage:
            st.info("data/audit/lineage.json not found. Run the pipeline with lineage enabled.")
        else:
            st.json(lineage)

    st.divider()
    st.markdown("### 📌 Current DQ snapshot (silver_dq.json)")
    dq_now = read_json_or_empty(SILVER_DQ_JSON_PATH)
    if dq_now:
        st.json(dq_now)
    else:
        st.info("silver_dq.json not found.")
