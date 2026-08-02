"""Thread-safe CSV persistence for neighborhood house statuses."""

from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import CSV_FIELDS, CSV_PATH, DEFAULT_HOUSES, STATUS_GREEN

_lock = threading.Lock()


def _ensure_data_dir() -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_csv(path: Path | None = None) -> Path:
    """Create the CSV with default houses if it does not exist."""
    target = path or CSV_PATH
    _ensure_data_dir()

    with _lock:
        if target.exists():
            return target

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            ts = _now_iso()
            for house_id in DEFAULT_HOUSES:
                writer.writerow(
                    {
                        "house_id": house_id,
                        "status_code": STATUS_GREEN,
                        "timestamp": ts,
                    }
                )
    return target


def read_all(path: Path | None = None) -> list[dict[str, str]]:
    """Return all rows sorted by house_id."""
    target = path or CSV_PATH
    init_csv(target)

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    return sorted(rows, key=lambda r: r["house_id"])


def update_status(
    house_id: str,
    status_code: str,
    path: Path | None = None,
) -> dict[str, str]:
    """Update one house status and return the updated row."""
    target = path or CSV_PATH
    init_csv(target)
    house_id = house_id.strip().upper()
    ts = _now_iso()

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        updated_row: dict[str, str] | None = None
        for row in rows:
            if row["house_id"].upper() == house_id:
                row["status_code"] = status_code
                row["timestamp"] = ts
                updated_row = row
                break

        if updated_row is None:
            updated_row = {
                "house_id": house_id,
                "status_code": status_code,
                "timestamp": ts,
            }
            rows.append(updated_row)

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: r["house_id"]))

    return updated_row


def apply_remote_update(
    house_id: str,
    status_code: str,
    path: Path | None = None,
) -> dict[str, str]:
    """Apply a status received from the mesh (same as local update)."""
    return update_status(house_id, status_code, path=path)
