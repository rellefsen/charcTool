"""Add, rename, and remove houses across local status and address stores."""

from __future__ import annotations

import re

from address_store import default_address, remove_address, rename_address, update_address
from config import STATUS_GREEN
from csv_store import read_all, remove_house as remove_status_house, rename_house as rename_status_house, update_status
from sync_state import read_last_sync, save_last_sync

HOUSE_ID_RE = re.compile(r"^[A-Z0-9]{1,8}$")


class HouseStoreError(ValueError):
    """Raised when house management operations fail validation."""


def normalize_house_id(house_id: str) -> str:
    return house_id.strip().upper()


def validate_house_id(house_id: str) -> None:
    normalized = normalize_house_id(house_id)
    if not normalized:
        raise HouseStoreError("House ID is required.")
    if not HOUSE_ID_RE.match(normalized):
        raise HouseStoreError(
            "House ID must be 1–8 letters or numbers (e.g. H001)."
        )


def house_exists(house_id: str) -> bool:
    house_id = normalize_house_id(house_id)
    return any(r["house_id"].upper() == house_id for r in read_all())


def suggest_next_house_id() -> str:
    """Suggest the next H### id based on existing houses."""
    numbers = []
    for row in read_all():
        match = re.search(r"H(\d+)$", row["house_id"].upper())
        if match:
            numbers.append(int(match.group(1)))
    next_num = max(numbers, default=0) + 1
    return f"H{next_num:03d}"


def _sync_last_sync_after_remove(house_id: str) -> None:
    house_id = normalize_house_id(house_id)
    last = read_last_sync()
    if house_id not in last:
        return
    del last[house_id]
    rows = [
        {"house_id": hid, "status_code": status, "timestamp": ""}
        for hid, status in sorted(last.items())
    ]
    if rows:
        save_last_sync(rows)
    else:
        from sync_state import clear_last_sync

        clear_last_sync()


def _sync_last_sync_after_rename(old_id: str, new_id: str) -> None:
    old_id = normalize_house_id(old_id)
    new_id = normalize_house_id(new_id)
    last = read_last_sync()
    if old_id not in last:
        return
    status = last.pop(old_id)
    last[new_id] = status
    rows = [
        {"house_id": hid, "status_code": status_code, "timestamp": ""}
        for hid, status_code in sorted(last.items())
    ]
    save_last_sync(rows)


def add_house(house_id: str, address: str | None = None) -> dict[str, str]:
    """Add a new house to the local status and address books."""
    house_id = normalize_house_id(house_id)
    validate_house_id(house_id)
    if house_exists(house_id):
        raise HouseStoreError(f"House {house_id} already exists.")

    row = update_status(house_id, STATUS_GREEN)
    addr = (address or default_address(house_id)).strip()
    update_address(house_id, addr)
    return row


def remove_house(house_id: str) -> None:
    """Remove a house from local status, addresses, and mesh sync baseline."""
    house_id = normalize_house_id(house_id)
    validate_house_id(house_id)
    if not house_exists(house_id):
        raise HouseStoreError(f"House {house_id} not found.")

    remove_status_house(house_id)
    remove_address(house_id)
    _sync_last_sync_after_remove(house_id)


def rename_house(old_id: str, new_id: str) -> dict[str, str]:
    """Rename a house across local stores."""
    old_id = normalize_house_id(old_id)
    new_id = normalize_house_id(new_id)
    validate_house_id(old_id)
    validate_house_id(new_id)

    if old_id == new_id:
        raise HouseStoreError("New house ID must be different.")
    if not house_exists(old_id):
        raise HouseStoreError(f"House {old_id} not found.")
    if house_exists(new_id):
        raise HouseStoreError(f"House {new_id} already exists.")

    row = rename_status_house(old_id, new_id)
    rename_address(old_id, new_id)
    _sync_last_sync_after_rename(old_id, new_id)
    return row
