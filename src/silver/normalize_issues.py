from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.common.logger import get_logger

logger = get_logger(__name__, layer="SILVER")

EXPECTED_COLS = [
    "issue_id",
    "issue_key",
    "issue_type",
    "assignee_name",
    "priority",
    "status",
    "created_at",
    "resolved_at",
]


def _first_dict(value: Any) -> Dict[str, Any]:
    """Helper: timestamps/assignee no seu JSON vem como lista com 1 dict."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    if isinstance(value, dict):
        return value
    return {}


def parse_datetime_with_status(value: Optional[str]) -> Tuple[Optional[str], str]:
    """
    Parse ISO datetime.
    Retorna (iso_string_normalizada_ou_None, status)

    status:
      - VALID
      - INVALID
      - MISSING
    """
    if value is None:
        return None, "MISSING"
    if not isinstance(value, str) or not value.strip():
        return None, "MISSING"

    v = value.strip()

    try:
        # Jira frequentemente usa "Z" (UTC). fromisoformat não aceita "Z" puro em várias versões.
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"

        dt = datetime.fromisoformat(v)
        return dt.isoformat(timespec="seconds"), "VALID"
    except Exception:
        logger.warning(f"Invalid datetime value encountered: {value!r}")
        return None, "INVALID"


def normalize_issues_to_silver(bronze_issues_path: str | Path):
    """
    Lê o JSON do Bronze e gera:
      - data/silver/silver_issues.csv (VALID)
      - data/silver/silver_issues_invalid.csv (MISSING/INVALID)
      - data/silver/silver_dq.json (resumo)

    Regras:
      - INVALID se:
          - created_at INVALID
          - resolved_at INVALID (quando preenchido)
          - resolved_at < created_at
      - MISSING se:
          - created_at MISSING
          - resolved_at MISSING
      - VALID caso contrário
    """
    bronze_issues_path = Path(bronze_issues_path)

    silver_dir = Path("data") / "silver"
    silver_dir.mkdir(parents=True, exist_ok=True)

    valid_path = silver_dir / "silver_issues.csv"
    invalid_path = silver_dir / "silver_issues_invalid.csv"
    dq_path = silver_dir / "silver_dq.json"

    raw = json.loads(bronze_issues_path.read_text(encoding="utf-8"))
    issues = raw.get("issues", [])
    if not isinstance(issues, list):
        issues = []

    valid_rows: List[Dict[str, Any]] = []
    invalid_rows: List[Dict[str, Any]] = []

    dq = {
        "total": 0,
        "valid": 0,
        "missing": 0,
        "invalid": 0,
        "created_missing": 0,
        "created_invalid": 0,
        "resolved_missing": 0,
        "resolved_invalid": 0,
        "resolved_before_created": 0,
        "issue_key_filled_from_id": 0,
    }

    for it in issues:
        if not isinstance(it, dict):
            continue

        dq["total"] += 1

        assignee = _first_dict(it.get("assignee"))
        ts = _first_dict(it.get("timestamps"))

        created_raw = ts.get("created_at")
        resolved_raw = ts.get("resolved_at")

        created_iso, created_status = parse_datetime_with_status(created_raw)
        resolved_iso, resolved_status = parse_datetime_with_status(resolved_raw)

        # ---- IDs (SEU CASO) ----
        # Seu JSON não tem "key", só "id" (ex: "JIRA-0002").
        issue_id = it.get("id")
        issue_key = it.get("key") or issue_id  # ✅ fallback definitivo

        if (it.get("key") is None or str(it.get("key")).strip() == "") and issue_id:
            dq["issue_key_filled_from_id"] += 1

        base_row = {
            "issue_id": issue_id,
            "issue_key": issue_key,
            "issue_type": it.get("issue_type"),
            "assignee_name": assignee.get("name") or "Unassigned",
            "priority": it.get("priority"),
            "status": it.get("status"),
            "created_at": created_iso or "",
            "resolved_at": resolved_iso or "",
        }

        # contadores por campo
        if created_status == "MISSING":
            dq["created_missing"] += 1
        elif created_status == "INVALID":
            dq["created_invalid"] += 1

        if resolved_status == "MISSING":
            dq["resolved_missing"] += 1
        elif resolved_status == "INVALID":
            dq["resolved_invalid"] += 1

        # 1) INVALID datetime
        if created_status == "INVALID" or resolved_status == "INVALID":
            dq["invalid"] += 1
            invalid_rows.append(
                {
                    **base_row,
                    "row_status": "INVALID",
                    "reason": "INVALID_DATETIME",
                    "raw_created_at": created_raw,
                    "raw_resolved_at": resolved_raw,
                    "created_status": created_status,
                    "resolved_status": resolved_status,
                }
            )
            continue

        # 2) MISSING datetime
        if created_status == "MISSING" or resolved_status == "MISSING":
            dq["missing"] += 1
            invalid_rows.append(
                {
                    **base_row,
                    "row_status": "MISSING",
                    "reason": "MISSING_DATETIME",
                    "raw_created_at": created_raw,
                    "raw_resolved_at": resolved_raw,
                    "created_status": created_status,
                    "resolved_status": resolved_status,
                }
            )
            continue

        # 3) Ambos VALID: checar ordem
        try:
            c_dt = datetime.fromisoformat(created_iso)  # type: ignore[arg-type]
            r_dt = datetime.fromisoformat(resolved_iso)  # type: ignore[arg-type]
            if r_dt < c_dt:
                dq["invalid"] += 1
                dq["resolved_before_created"] += 1
                invalid_rows.append(
                    {
                        **base_row,
                        "row_status": "INVALID",
                        "reason": "RESOLVED_BEFORE_CREATED",
                        "raw_created_at": created_raw,
                        "raw_resolved_at": resolved_raw,
                        "created_status": created_status,
                        "resolved_status": resolved_status,
                    }
                )
                continue
        except Exception:
            dq["invalid"] += 1
            invalid_rows.append(
                {
                    **base_row,
                    "row_status": "INVALID",
                    "reason": "ORDER_CHECK_FAILED",
                    "raw_created_at": created_raw,
                    "raw_resolved_at": resolved_raw,
                    "created_status": created_status,
                    "resolved_status": resolved_status,
                }
            )
            continue

        # 4) VALID
        valid_rows.append(base_row)

    df_valid = pd.DataFrame(valid_rows)
    df_invalid = pd.DataFrame(invalid_rows)

    # Garantir schema do valid
    if df_valid.empty:
        df_valid = pd.DataFrame(columns=EXPECTED_COLS)
    else:
        for c in EXPECTED_COLS:
            if c not in df_valid.columns:
                df_valid[c] = ""
        df_valid = df_valid[EXPECTED_COLS]

    df_valid.to_csv(valid_path, index=False, encoding="utf-8")
    df_invalid.to_csv(invalid_path, index=False, encoding="utf-8")

    dq["valid"] = int(len(df_valid))
    dq_path.write_text(json.dumps(dq, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(f"✅ Silver VALID: {dq['valid']}")
    logger.info(f"⚠️ Silver MISSING: {dq['missing']}")
    logger.info(f"❌ Silver INVALID: {dq['invalid']}")
    logger.info(f"🔑 issue_key filled from id: {dq['issue_key_filled_from_id']}")

    return valid_path, invalid_path, dq_path


def main() -> None:
    bronze_path = Path("data") / "bronze" / "jira_issues_raw.json"
    if not bronze_path.exists():
        raise SystemExit(f"Bronze file not found: {bronze_path}")

    normalize_issues_to_silver(bronze_path)


if __name__ == "__main__":
    main()
