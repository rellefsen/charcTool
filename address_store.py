"""Local house address storage — UI only, never sent over the mesh."""

from __future__ import annotations

import csv
import re
import threading
from pathlib import Path

from config import (
    ADDRESS_FIELDS,
    ADDRESSES_PATH,
    DEFAULT_HOUSES,
    DEFAULT_STREET_NAME,
)

_lock = threading.Lock()
_HOUSE_NUM = re.compile(r"(\d+)$")


def _ensure_data_dir() -> None:
    ADDRESSES_PATH.parent.mkdir(parents=True, exist_ok=True)


def default_address(house_id: str) -> str:
    """Generate a placeholder street address from a house id like H001."""
    match = _HOUSE_NUM.search(house_id.strip().upper())
    number = int(match.group(1)) if match else 0
    return f"{number} {DEFAULT_STREET_NAME}"


def init_addresses(path: Path | None = None) -> Path:
    target = path or ADDRESSES_PATH
    _ensure_data_dir()

    with _lock:
        if target.exists():
            return target

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=ADDRESS_FIELDS)
            writer.writeheader()
            for house_id in DEFAULT_HOUSES:
                writer.writerow({"house_id": house_id, "address": default_address(house_id)})

    return target


def ensure_default_addresses(path: Path | None = None) -> int:
    """Add address rows for any houses missing from the local address book."""
    target = path or ADDRESSES_PATH
    init_addresses(target)

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        existing = {row["house_id"].upper() for row in rows}
        added = 0
        for house_id in DEFAULT_HOUSES:
            if house_id not in existing:
                rows.append({"house_id": house_id, "address": default_address(house_id)})
                added += 1

        if added:
            with target.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=ADDRESS_FIELDS)
                writer.writeheader()
                writer.writerows(sorted(rows, key=lambda r: r["house_id"]))

    return added


def read_address_map(path: Path | None = None) -> dict[str, str]:
    target = path or ADDRESSES_PATH
    init_addresses(target)

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    return {row["house_id"].upper(): row["address"] for row in rows}


def update_address(house_id: str, address: str, path: Path | None = None) -> dict[str, str]:
    target = path or ADDRESSES_PATH
    init_addresses(target)
    house_id = house_id.strip().upper()
    address = address.strip()

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        updated: dict[str, str] | None = None
        for row in rows:
            if row["house_id"].upper() == house_id:
                row["address"] = address
                updated = row
                break

        if updated is None:
            updated = {"house_id": house_id, "address": address}
            rows.append(updated)

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=ADDRESS_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: r["house_id"]))

    return updated


def remove_address(house_id: str, path: Path | None = None) -> None:
    target = path or ADDRESSES_PATH
    init_addresses(target)
    house_id = house_id.strip().upper()

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        new_rows = [r for r in rows if r["house_id"].upper() != house_id]
        if len(new_rows) == len(rows):
            return

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=ADDRESS_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(new_rows, key=lambda r: r["house_id"]))


def rename_address(old_id: str, new_id: str, path: Path | None = None) -> None:
    target = path or ADDRESSES_PATH
    init_addresses(target)
    old_id = old_id.strip().upper()
    new_id = new_id.strip().upper()

    with _lock:
        with target.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        for row in rows:
            if row["house_id"].upper() == old_id:
                row["house_id"] = new_id
                break

        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=ADDRESS_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: r["house_id"]))


def attach_addresses(
    rows: list[dict[str, str]],
    path: Path | None = None,
) -> list[dict[str, str]]:
    """Add an address field to status rows for UI display."""
    address_map = read_address_map(path)
    enriched: list[dict[str, str]] = []
    for row in rows:
        house_id = row["house_id"].upper()
        enriched.append(
            {
                **row,
                "address": address_map.get(house_id, default_address(house_id)),
            }
        )
    return enriched
