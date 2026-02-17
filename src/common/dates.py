from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class DateParseResult:
    value : Optional[datetime]
    status: str  # VALID | INVALID | MISSING
    reason: Optional[str] = None


def _from_epoch(value: int) -> datetime:
    # Heuristic: 13 digits => ms, 10 digits => seconds
    if value > 10_000_000_000:  # ms
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return datetime.fromtimestamp(value, tz=timezone.utc)


def parse_datetime_with_status(value: object) -> DateParseResult:
    """
    Robust datetime parsing for Jira exports.

    Accepts:
      - ISO 8601 with Z: 2026-02-01T10:00:00Z
      - ISO 8601 with offsets: 2026-02-01T10:00:00.000-0300 / -03:00 / +0000
      - Epoch seconds/ms: 1700000000 or 1700000000000
      - Returns VALID/INVALID/MISSING (never raises).
    """
    if value is None:
        return DateParseResult(None, "MISSING", "missing_datetime")

    # Epoch timestamps (int/float or numeric string)
    if isinstance(value, (int, float)):
        try:
            dt = _from_epoch(int(value))
            return DateParseResult(dt, "VALID", None)
        except Exception:
            return DateParseResult(None, "INVALID", "invalid_epoch_datetime")

    if isinstance(value, str) and value.strip().isdigit():
        try:
            dt = _from_epoch(int(value.strip()))
            return DateParseResult(dt, "VALID", None)
        except Exception:
            return DateParseResult(None, "INVALID", "invalid_epoch_datetime")

    if not isinstance(value, str):
        return DateParseResult(None, "MISSING", "datetime_not_a_string")

    raw = value.strip()
    if raw == "":
        return DateParseResult(None, "MISSING", "empty_datetime")

    try:
        # Z suffix => UTC
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return DateParseResult(dt.astimezone(timezone.utc), "VALID", None)

        # Handle "+0000" / "-0300" offsets by inserting colon => "+00:00"
        if len(raw) >= 5 and (raw[-5] in ["+", "-"]) and raw[-4:].isdigit():
            raw = raw[:-2] + ":" + raw[-2:]

        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return DateParseResult(dt.astimezone(timezone.utc), "VALID", None)

    except ValueError:
        return DateParseResult(None, "INVALID", "invalid_datetime_format")


# Canonical alias used by other modules
def parse_iso_datetime(value: object) -> DateParseResult:
    return parse_datetime_with_status(value)
