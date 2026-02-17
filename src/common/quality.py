from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ValidationError:
    rule: str
    message: str


def validate_required_str(value: object, field_name: str) -> Optional[ValidationError]:
    if value is None:
        return ValidationError("required_field_missing", f"{field_name} is missing")
    if not isinstance(value, str):
        return ValidationError("invalid_type", f"{field_name} must be a string")
    if value.strip() == "":
        return ValidationError("empty_string", f"{field_name} is empty")
    return None
