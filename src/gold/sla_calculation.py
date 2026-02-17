"""src/gold/sla_calculation.py

Calcula o tempo de resolução em "horas úteis" entre start e end.

Motivo:
- O desafio exige excluir finais de semana e feriados nacionais.
- Adotamos "dia útil = 24h", sem restringir horário comercial.

Regras:
- Considera apenas dias de segunda a sexta (pd.bdate_range).
- Remove feriados nacionais informados em `holidays`.
- Ajusta naturalmente pelo intervalo:
    • Primeiro dia: não conta horas antes de `start`.
    • Último dia: não conta horas após `end`.

Observação:
- Não considera feriados regionais.
- Modelo adotado exclusivamente para atender ao escopo do desafio.

SLA expected hours (FIXO do desafio):
- High=24, Medium=72, Low=120
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional

import pandas as pd

from src.common.logger import get_logger

logger = get_logger(__name__, layer="GOLD")


# -----------------------------
# Configuration
# -----------------------------
# Targets FIXOS do desafio (NÃO escalar)
_SLA_RULES = {"High": 24.0, "Medium": 72.0, "Low": 120.0}

# No challenge-based working window: a business day counts as 24h.
# Kept as a constant for debugging/logging consistency.
BUSINESS_DAY_HOURS: float = 24.0


def sla_expected_hours(priority: str | None) -> Optional[float]:
    """
    Return expected SLA hours for a priority.

    IMPORTANT:
    - returns FIXED targets (24/72/120), independent of BUSINESS_DAY_HOURS
    - tolerant to casing and common typo "Mediun"
    """
    if priority is None:
        return None

    p = str(priority).strip()
    if not p:
        return None

    # tolerate typo
    if p.lower() == "mediun":
        p = "Medium"

    # direct
    base = _SLA_RULES.get(p)
    if base is not None:
        return float(base)

    # tolerate casing
    base = _SLA_RULES.get(p.title())
    if base is not None:
        return float(base)

    base = _SLA_RULES.get(p.capitalize())
    if base is not None:
        return float(base)

    return None


def sla_met(resolution_hours: float | None, expected_hours: float | None) -> bool:
    """True if resolution_hours <= expected_hours, handling None safely."""
    if resolution_hours is None or expected_hours is None:
        return False
    try:
        return float(resolution_hours) <= float(expected_hours)
    except Exception:
        return False


def normalize_holidays(values: Iterable[object]) -> set[date]:
    """Normalize a list/series of dates into a set[date]."""
    out: set[date] = set()
    for v in values:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        try:
            ts = pd.to_datetime(v, errors="coerce", utc=True)
        except Exception:
            ts = pd.NaT
        if pd.isna(ts):
            continue
        if isinstance(ts, pd.DatetimeIndex):
            for x in ts:
                if not pd.isna(x):
                    out.add(x.date())
        else:
            out.add(ts.date())
    return out


def calculate_business_hours(
    start: pd.Timestamp,
    end: pd.Timestamp,
    holidays: set[date],
) -> float | None:
    """Calcula horas "úteis" (dia útil = 24h) entre start e end.

    - Conta somente o tempo que cai em dias úteis (Seg–Sex)
    - Exclui feriados nacionais (pelas datas em `holidays`)
    - Não restringe horário comercial
    """
    if pd.isna(start) or pd.isna(end) or end < start:
        return None

    # Ensure timezone-aware timestamps behave consistently
    start = pd.to_datetime(start, errors="coerce", utc=True)
    end = pd.to_datetime(end, errors="coerce", utc=True)

    if pd.isna(start) or pd.isna(end) or end < start:
        return None

    start_date = start.date()
    end_date = end.date()

    # Business days between start_date and end_date inclusive, minus holidays
    bdays = pd.bdate_range(start=start_date, end=end_date)
    bdays = [d.date() for d in bdays if d.date() not in holidays]
    if not bdays:
        return 0.0

    total_seconds: float = 0.0

    # Compute day-by-day overlap against [start, end]
    # start/end are UTC-aware (we coercively parsed with utc=True)
    start_dt = start.to_pydatetime()
    end_dt = end.to_pydatetime()

    for day in bdays:
        # Build UTC-aware day boundaries to avoid naive/aware comparisons
        day_start = pd.Timestamp(datetime.combine(day, datetime.min.time()), tz="UTC").to_pydatetime()
        day_end = (pd.Timestamp(day_start) + pd.Timedelta(days=1)).to_pydatetime()

        seg_start = max(start_dt, day_start)
        seg_end = min(end_dt, day_end)

        if seg_end > seg_start:
            total_seconds += (seg_end - seg_start).total_seconds()

    return max(total_seconds / 3600.0, 0.0)


__all__ = [
    "BUSINESS_DAY_HOURS",
    "calculate_business_hours",
    "normalize_holidays",
    "sla_expected_hours",
    "sla_met",
]
