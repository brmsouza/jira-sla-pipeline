from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

# garante que a RAIZ do repo entra no sys.path
# (2 níveis acima de src/gold = raiz do projeto)
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.gold import sla_calculation as sc  # noqa: E402


def main() -> None:
    print("=== DEBUG SLA JIRA-0759 ===")
    print("Loaded module:", sc.__file__)
    print("BUSINESS_DAY_HOURS:", getattr(sc, "BUSINESS_DAY_HOURS", "N/A"))

    # Linha do seu CSV (para comparação / conferência)
    row = {
        "key": "JIRA-0759",
        "issue_id": None,
        "issue_type": "Task",
        "assignee_name": "Francinne",
        "priority": "Medium",
        "status": "Resolved",
        "created_at": "2025-09-07 23:36:09+00:00",
        "resolved_at": "2025-09-11 03:36:09+00:00",
        # valores que estavam saindo no seu gold/csv (exemplo que você mandou)
        "csv_sla_expected_hours": 24.0,
        "csv_resolution_hours": 25.200833333333332,
        "csv_is_sla_met": False,
        "csv_sla_status": "BREACHED",
    }

    holidays_path = ROOT / "data" / "bronze" / "bronze_holidays.csv"
    if not holidays_path.exists():
        raise SystemExit(f"ERROR: holidays file not found: {holidays_path}")

    hdf = pd.read_csv(holidays_path)
    if "date" not in hdf.columns:
        raise SystemExit("ERROR: bronze_holidays.csv must have column 'date'")

    holidays = set(pd.to_datetime(hdf["date"], errors="coerce", utc=True).dt.date.dropna().tolist())

    print("\n--- Holidays ---")
    print("Holidays loaded:", len(holidays))

    start = pd.Timestamp(row["created_at"])
    end = pd.Timestamp(row["resolved_at"])

    print("\n--- Input ---")
    print("Key:", row["key"])
    print("Priority:", row["priority"])
    print("Status:", row["status"])
    print("Created:", start)
    print("Resolved:", end)

    # cálculo correto pelo módulo
    hours = sc.calculate_business_hours(start, end, holidays)

    expected = sc.sla_expected_hours("Medium")
    met = sc.sla_met(hours, expected)

    print("\n--- Computed by code ---")
    print("Computed business hours:", hours)
    print("Expected SLA hours (by priority):", expected)
    print("SLA met?:", met)
    print("Computed SLA status:", "MET" if met else "BREACHED")

    # comparação com o que estava no CSV que você mostrou
    print("\n--- What your CSV showed (example) ---")
    print("CSV sla_expected_hours:", row["csv_sla_expected_hours"])
    print("CSV resolution_hours:", row["csv_resolution_hours"])
    print("CSV is_sla_met:", row["csv_is_sla_met"])
    print("CSV sla_status:", row["csv_sla_status"])

    # diferenças para facilitar enxergar o erro
    try:
        diff_expected = float(expected) - float(row["csv_sla_expected_hours"])
    except Exception:
        diff_expected = None

    try:
        diff_hours = float(hours) - float(row["csv_resolution_hours"])
    except Exception:
        diff_hours = None

    print("\n--- Deltas (computed - csv) ---")
    print("Δ expected_hours:", diff_expected)
    print("Δ resolution_hours:", diff_hours)

    # Checagem final (o correto para Medium é 72h)
    print("\n--- Sanity ---")
    print("Sanity expected Medium should be 72:", sc.sla_expected_hours("Medium"))


if __name__ == "__main__":
    main()
