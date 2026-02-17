from __future__ import annotations

import json
from pathlib import Path
from typing  import Any

import pandas as pd


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: str) -> Any:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str, payload: Any, indent: int = 2) -> None:
    p = Path(path)
    ensure_parent_dir(p)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")


def read_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def write_csv(path: str, df: pd.DataFrame) -> None:
    p = Path(path)
    ensure_parent_dir(p)
    df.to_csv(p, index=False)
