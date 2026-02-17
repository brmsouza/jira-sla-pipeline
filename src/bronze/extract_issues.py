from __future__ import annotations

import os
from pathlib import Path

from src.common.logger import get_logger

logger = get_logger(__name__, layer="BRONZE")


def extract_issues_to_bronze() -> Path:
    """
    Writes raw Jira issues JSON to:
      data/bronze/jira_issues_raw.json

    Supports:
      1) BRONZE_ISSUES_SOURCE_JSON env var (path to json)
      2) fallback: data/source/jira_issues_raw.json  ✅ (SEU CASO)
      3) fallback: project root jira_issues_raw.json
      4) fallback: keep existing if already generated
    """
    base_dir = Path(__file__).resolve().parents[2]

    out_dir = base_dir / "data" / "bronze"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "jira_issues_raw.json"

    # 0) if already exists, keep it (idempotent)
    if out_path.exists() and out_path.stat().st_size > 0:
        logger.info(f"🟫 Bronze issues already exists: {out_path}")
        return out_path

    # 1) env var source
    env_source = os.getenv("BRONZE_ISSUES_SOURCE_JSON", "").strip()
    if env_source:
        src = Path(env_source)
        if not src.is_absolute():
            src = (base_dir / src).resolve()
        if src.exists() and src.stat().st_size > 0:
            out_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info(f"🟫 Bronze issues copied from env source: {src} -> {out_path}")
            return out_path
        logger.warning(f"BRONZE_ISSUES_SOURCE_JSON set but file not found/empty: {src}")

    # 2) fallback: data/source/jira_issues_raw.json  ✅
    source_json = base_dir / "data" / "source" / "jira_issues_raw.json"
    if source_json.exists() and source_json.stat().st_size > 0:
        out_path.write_text(source_json.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info(f"🟫 Bronze issues copied from data/source: {source_json} -> {out_path}")
        return out_path

    # 3) fallback: project root
    root_json = base_dir / "jira_issues_raw.json"
    if root_json.exists() and root_json.stat().st_size > 0:
        out_path.write_text(root_json.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info(f"🟫 Bronze issues copied from repo root: {root_json} -> {out_path}")
        return out_path

    msg = (
        "Bronze extract did not find a source JSON to write.\n"
        f"Expected to write: {out_path}\n"
        "Tried:\n"
        "  - ENV BRONZE_ISSUES_SOURCE_JSON\n"
        "  - data/source/jira_issues_raw.json\n"
        "  - repo root jira_issues_raw.json\n"
    )
    logger.error(msg)
    raise FileNotFoundError(msg)
