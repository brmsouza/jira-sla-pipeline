# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- (Planned) Reduce repeated warning logs for invalid datetime parsing (log first N examples + summary).
- (Planned) Add a small anonymized sample dataset under `data/source/jira_issues_raw.sample.json`.

### Changed
- (Planned) Improve execution performance of business-hour calculations.
- (Planned) Introduce optional CI validation for Quality Gate enforcement.

### Fixed
- (Planned) Windows/OneDrive log file lock handling during cleanup (optional log rotation).

---

## [1.1.0] - 2026-02-17

### Added
- Professional portfolio-level README redesign.
- Animated dashboard preview (`docs/images/dashboard_demo.gif`).
- Enterprise-style documentation layout.

### Changed
- Reorganized README as a structured landing page.
- Improved architectural explanation (Medallion + Governance emphasis).
- Strengthened project positioning as a production-grade data platform.

---

## [1.0.1] - 2026-02-17

### Added
- Streamlit dark theme configuration via `.streamlit/config.toml`.

### Changed
- Dashboard charts and layout aligned to dark theme (Plotly + Streamlit).

---

## [1.0.0] - 2026-02-17

### Added
- Medallion architecture (Bronze / Silver / Gold) with a Python pipeline orchestrator.
- Bronze ingestion:
  - Jira issues raw dataset copied into `data/bronze/jira_issues_raw.json`.
  - Brazilian national holidays extraction to `data/bronze/bronze_holidays.csv`.
- Silver processing:
  - Schema normalization and datetime parsing with data quality classification (VALID / MISSING / INVALID).
  - Business calendar generation excluding weekends and holidays (`data/silver/silver_calendar.csv`).
- Gold analytics:
  - Resolved SLA dataset (`data/gold/gold_sla_issues.csv`) with SLA evaluation fields.
  - Backlog SLA dataset (`data/gold/gold_sla_backlog.csv`) including business-age and overdue flag.
  - Mandatory aggregated reports:
    - `data/gold/gold_sla_by_analyst.csv`
    - `data/gold/gold_sla_by_issue_type.csv`
- Quality Gate:
  - `validate_all.py` validates required Gold outputs, schema, non-empty datasets, and basic numeric sanity checks.

### Changed
- Standardized project documentation and scripts to English (comments/docstrings/messages).

### Fixed
- Improved run reliability by making outputs deterministic and validating deliverables via the Quality Gate.
