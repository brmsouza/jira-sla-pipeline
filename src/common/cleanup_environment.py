"""
Cleanup Environment Script

Removes generated CSV and LOG files before pipeline execution.

Purpose:
- Guarantee that output files were generated in the current run.
- Avoid stale data.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"


def remove_files_by_extension(base_path: Path, extension: str) -> None:
    if not base_path.exists():
        return

    for file in base_path.rglob(f"*{extension}"):
        try:
            file.unlink()
            print(f"🗑 Removed: {file.relative_to(PROJECT_ROOT)}")
        except Exception as e:
            print(f"⚠ Failed to remove {file}: {e}")


def cleanup() -> None:
    print("\n🧹 Cleaning previous execution files...\n")

    remove_files_by_extension(DATA_DIR, ".csv")
    remove_files_by_extension(LOGS_DIR, ".log")

    print("\n✅ Cleanup completed.\n")


if __name__ == "__main__":
    cleanup()
