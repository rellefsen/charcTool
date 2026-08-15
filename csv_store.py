"""Thread-safe CSV persistence for neighborhood house statuses."""

from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import CSV_FIELDS, CSV_PATH, DEFAULT_HOUSES, STATUS_GREEN, STATUS_RED, STATUS_URGENCY, STATUS_YELLOW

_lock = threading.Lock()


def _ensure_data_dir() -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def ensure_status_csv(path: Path | None = None) -> Path:
    """Create an empty status CSV (header only) if it does not exist."""
    target = path or CSV_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        if target.exists():
            return target

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
    return target


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


def ensure_default_houses(path: Path | None = None) -> int:
    """Add any missing DEFAULT_HOUSES rows to an existing CSV. Returns count added."""
    target = path or CSV_PATH
    init_csv(target)
    ts = _now_iso()

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        existing = {row["house_id"].upper() for row in rows}
        added = 0
        for house_id in DEFAULT_HOUSES:
            if house_id not in existing:
                rows.append(
                    {
                        "house_id": house_id,
                        "status_code": STATUS_GREEN,
                        "timestamp": ts,
                    }
                )
                added += 1

        if added:
            with target.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(sorted(rows, key=lambda r: r["house_id"]))

    return added


def read_all(path: Path | None = None) -> list[dict[str, str]]:
    """Return all rows sorted by house_id."""
    target = path or CSV_PATH
    ensure_status_csv(target)

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    return sorted(rows, key=lambda r: r["house_id"])


def sort_rows_by_urgency(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Sort houses by urgency (RED, YELLOW, BLACK, GREEN); ties by house_id."""
    return sorted(
        rows,
        key=lambda r: (
            STATUS_URGENCY.get(r["status_code"], 99),
            r["house_id"],
        ),
    )


def update_status(
    house_id: str,
    status_code: str,
    path: Path | None = None,
) -> dict[str, str]:
    """Update one house status and return the updated row."""
    target = path or CSV_PATH
    ensure_status_csv(target)
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


def remove_house(house_id: str, path: Path | None = None) -> None:
    """Remove a house from the status CSV."""
    target = path or CSV_PATH
    ensure_status_csv(target)
    house_id = house_id.strip().upper()

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        new_rows = [r for r in rows if r["house_id"].upper() != house_id]
        if len(new_rows) == len(rows):
            raise ValueError(f"House {house_id} not found")

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(new_rows, key=lambda r: r["house_id"]))


def rename_house(old_id: str, new_id: str, path: Path | None = None) -> dict[str, str]:
    """Rename a house id in the status CSV."""
    target = path or CSV_PATH
    ensure_status_csv(target)
    old_id = old_id.strip().upper()
    new_id = new_id.strip().upper()

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        if not any(r["house_id"].upper() == old_id for r in rows):
            raise ValueError(f"House {old_id} not found")
        if any(r["house_id"].upper() == new_id for r in rows):
            raise ValueError(f"House {new_id} already exists")

        updated_row: dict[str, str] | None = None
        for row in rows:
            if row["house_id"].upper() == old_id:
                row["house_id"] = new_id
                updated_row = row
                break

        assert updated_row is not None

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


def reconcile_non_green_snapshot(
    snapshot_house_ids: set[str],
    path: Path | None = None,
    *,
    snapshot_at: str | None = None,
    previous_heartbeat_at: str | None = None,
) -> list[str]:
    """
    After a heartbeat snapshot, set local RED/YELLOW houses not in the snapshot to GREEN.

    BLACK is never auto-cleared: a missing death is a lost packet, not a GREEN.
    Houses whose local timestamp is newer than this snapshot (or newer than the
    previous completed heartbeat) are kept — a captain packet that reached EOC
    but not the precinct must not be wiped.
    Returns house IDs that were cleared.
    """
    target = path or CSV_PATH
    ensure_status_csv(target)
    snapshot = {house_id.strip().upper() for house_id in snapshot_house_ids}
    clearable = {STATUS_RED, STATUS_YELLOW}
    snapshot_dt = _parse_iso(snapshot_at)
    previous_dt = _parse_iso(previous_heartbeat_at)

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        cleared: list[str] = []
        ts = _now_iso()
        for row in rows:
            house_id = row["house_id"].upper()
            if row["status_code"] not in clearable:
                continue
            if house_id in snapshot:
                continue
            local_dt = _parse_iso(row.get("timestamp"))
            if local_dt is not None:
                if snapshot_dt is not None and local_dt > snapshot_dt:
                    continue
                if previous_dt is not None and local_dt > previous_dt:
                    continue
            row["status_code"] = STATUS_GREEN
            row["timestamp"] = ts
            cleared.append(house_id)

        if cleared:
            with target.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(sorted(rows, key=lambda r: r["house_id"]))

    return sorted(cleared)
