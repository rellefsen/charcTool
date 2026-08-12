"""Track what was last transmitted to the mesh for delta sync."""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from pathlib import Path

from config import CSV_FIELDS, LAST_SYNC_PATH, STATUS_URGENCY

_lock = threading.Lock()


@dataclass(frozen=True)
class HouseChange:
    house_id: str
    previous_status: str | None
    current_status: str
    timestamp: str


def has_last_sync(path: Path | None = None) -> bool:
    target = path or LAST_SYNC_PATH
    return target.exists()


def read_last_sync(path: Path | None = None) -> dict[str, str]:
    """Return house_id -> status_code for the last successful mesh sync."""
    target = path or LAST_SYNC_PATH
    if not target.exists():
        return {}

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    return {row["house_id"].upper(): row["status_code"] for row in rows}


def save_last_sync(rows: list[dict[str, str]], path: Path | None = None) -> None:
    """Persist the board state after a successful mesh transmission."""
    target = path or LAST_SYNC_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: r["house_id"]))


def clear_last_sync(path: Path | None = None) -> None:
    target = path or LAST_SYNC_PATH
    if target.exists():
        target.unlink()


def compute_sync_rows(
    current_rows: list[dict[str, str]],
    force_full: bool = False,
    last_sync_path: Path | None = None,
) -> tuple[list[tuple[str, str]], str]:
    """
    Decide which houses to transmit.

    Returns ([(house_id, status_code), ...], sync_mode_label).
    sync_mode_label is 'full', 'delta', or 'none'.
    """
    if force_full or not has_last_sync(last_sync_path):
        if not current_rows:
            return [], "none"
        return [(r["house_id"], r["status_code"]) for r in current_rows], "full"

    last = read_last_sync(last_sync_path)
    changes: list[tuple[str, str]] = []

    for row in current_rows:
        house_id = row["house_id"].upper()
        status = row["status_code"]
        if last.get(house_id) != status:
            changes.append((house_id, status))

    if not changes:
        return [], "none"
    return changes, "delta"


def compute_non_green_rows(
    current_rows: list[dict[str, str]],
) -> list[tuple[str, str]]:
    """Return (house_id, status_code) for every house that is not GREEN."""
    from config import STATUS_GREEN

    return [
        (row["house_id"], row["status_code"])
        for row in current_rows
        if row["status_code"].upper() != STATUS_GREEN
    ]


def compute_changes_since_baseline(
    current_rows: list[dict[str, str]],
    baseline: dict[str, str],
) -> list[HouseChange]:
    """Return houses whose status differs from the receiver watch baseline."""
    changes: list[HouseChange] = []

    for row in current_rows:
        house_id = row["house_id"].upper()
        current_status = row["status_code"]
        previous_status = baseline.get(house_id)

        if previous_status is None:
            changes.append(
                HouseChange(
                    house_id=house_id,
                    previous_status=None,
                    current_status=current_status,
                    timestamp=row.get("timestamp", ""),
                )
            )
        elif previous_status != current_status:
            changes.append(
                HouseChange(
                    house_id=house_id,
                    previous_status=previous_status,
                    current_status=current_status,
                    timestamp=row.get("timestamp", ""),
                )
            )

    return sorted(changes, key=lambda c: c.house_id)


def format_status_change(previous_status: str | None, current_status: str) -> str:
    """Format a receiver change for display, e.g. GREEN → RED."""
    before = previous_status if previous_status else "NEW"
    return f"{before} → {current_status}"


def sort_changes_by_urgency(changes: list[HouseChange]) -> list[HouseChange]:
    """Sort pending receiver changes by current status urgency."""
    return sorted(
        changes,
        key=lambda c: (STATUS_URGENCY.get(c.current_status, 99), c.house_id),
    )


def rows_to_baseline(rows: list[dict[str, str]]) -> dict[str, str]:
    return {row["house_id"].upper(): row["status_code"] for row in rows}
