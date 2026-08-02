"""District and precinct organization with per-precinct data paths."""

from __future__ import annotations

import json
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config

_lock = threading.Lock()

DISTRICT_ID_RE = re.compile(r"^[A-Z0-9]{2,8}$")
PRECINCT_SUFFIX_RE = re.compile(r"^[A-Z0-9]{2,4}$")
PRECINCT_ID_RE = re.compile(r"^[A-Z0-9]{4,12}$")


class PrecinctStoreError(ValueError):
    """Raised when organization or precinct operations fail."""


@dataclass(frozen=True)
class District:
    id: str
    name: str


@dataclass(frozen=True)
class Precinct:
    id: str
    district_id: str
    name: str


@dataclass(frozen=True)
class PrecinctPaths:
    precinct_id: str
    status: Path
    addresses: Path
    last_sync: Path


def normalize_id(value: str) -> str:
    return value.strip().upper()


def make_precinct_id(district_id: str, suffix: str) -> str:
    district_id = normalize_id(district_id)
    suffix = normalize_id(suffix)
    validate_district_id(district_id)
    if not PRECINCT_SUFFIX_RE.match(suffix):
        raise PrecinctStoreError(
            "Precinct suffix must be 2–4 letters or numbers (e.g. 01, A1)."
        )
    precinct_id = f"{district_id}{suffix}"
    if not PRECINCT_ID_RE.match(precinct_id):
        raise PrecinctStoreError(f"Invalid precinct id: {precinct_id}")
    return precinct_id


def validate_district_id(district_id: str) -> None:
    district_id = normalize_id(district_id)
    if not DISTRICT_ID_RE.match(district_id):
        raise PrecinctStoreError(
            "District ID must be 2–8 letters or numbers (e.g. CHARC)."
        )


def validate_precinct_id(precinct_id: str, organization: dict[str, Any] | None = None) -> None:
    precinct_id = normalize_id(precinct_id)
    if not PRECINCT_ID_RE.match(precinct_id):
        raise PrecinctStoreError(
            "Precinct ID must be 4–12 letters or numbers (district + suffix)."
        )
    org = organization or load_organization()
    if precinct_id not in {p["id"] for p in org.get("precincts", [])}:
        raise PrecinctStoreError(f"Unknown precinct: {precinct_id}")


def precinct_dir(precinct_id: str) -> Path:
    return config.PRECINCTS_DIR / normalize_id(precinct_id)


def paths_for_precinct(precinct_id: str) -> PrecinctPaths:
    precinct_id = normalize_id(precinct_id)
    base = precinct_dir(precinct_id)
    return PrecinctPaths(
        precinct_id=precinct_id,
        status=base / "neighborhood_status.csv",
        addresses=base / "house_addresses.csv",
        last_sync=base / "last_mesh_sync.csv",
    )


def _default_organization() -> dict[str, Any]:
    return {
        "districts": [
            {
                "id": config.DEFAULT_DISTRICT_ID,
                "name": config.DEFAULT_DISTRICT_NAME,
            }
        ],
        "precincts": [
            {
                "id": config.DEFAULT_PRECINCT_ID,
                "district_id": config.DEFAULT_DISTRICT_ID,
                "name": config.DEFAULT_PRECINCT_NAME,
            }
        ],
    }


def load_organization() -> dict[str, Any]:
    target = config.ORGANIZATION_PATH
    with _lock:
        if not target.exists():
            return _default_organization()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _default_organization()
        if not isinstance(raw, dict):
            return _default_organization()
        districts = raw.get("districts") or []
        precincts = raw.get("precincts") or []
        if not districts or not precincts:
            return _default_organization()
        return {"districts": districts, "precincts": precincts}


def save_organization(org: dict[str, Any]) -> None:
    config.ORGANIZATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        config.ORGANIZATION_PATH.write_text(
            json.dumps(org, indent=2) + "\n",
            encoding="utf-8",
        )


def init_organization() -> dict[str, Any]:
    org = load_organization()
    if not config.ORGANIZATION_PATH.exists():
        save_organization(org)
    for precinct in org["precincts"]:
        precinct_dir(precinct["id"]).mkdir(parents=True, exist_ok=True)
    return org


def list_districts() -> list[District]:
    org = load_organization()
    return [
        District(id=normalize_id(d["id"]), name=str(d.get("name", d["id"])))
        for d in org.get("districts", [])
    ]


def list_precincts(district_id: str | None = None) -> list[Precinct]:
    org = load_organization()
    district_id = normalize_id(district_id) if district_id else None
    precincts: list[Precinct] = []
    for row in org.get("precincts", []):
        pid = normalize_id(row["id"])
        did = normalize_id(row["district_id"])
        if district_id and did != district_id:
            continue
        precincts.append(
            Precinct(id=pid, district_id=did, name=str(row.get("name", pid)))
        )
    return sorted(precincts, key=lambda p: p.id)


def get_precinct(precinct_id: str) -> Precinct | None:
    precinct_id = normalize_id(precinct_id)
    for precinct in list_precincts():
        if precinct.id == precinct_id:
            return precinct
    return None


def get_district_for_precinct(precinct_id: str) -> str:
    precinct = get_precinct(precinct_id)
    if precinct is None:
        raise PrecinctStoreError(f"Unknown precinct: {precinct_id}")
    return precinct.district_id


def add_precinct(district_id: str, suffix: str, name: str) -> Precinct:
    district_id = normalize_id(district_id)
    validate_district_id(district_id)
    precinct_id = make_precinct_id(district_id, suffix)
    org = load_organization()
    existing = {normalize_id(p["id"]) for p in org.get("precincts", [])}
    if precinct_id in existing:
        raise PrecinctStoreError(f"Precinct {precinct_id} already exists.")

    district_ids = {normalize_id(d["id"]) for d in org.get("districts", [])}
    if district_id not in district_ids:
        raise PrecinctStoreError(f"Unknown district: {district_id}")

    org["precincts"].append(
        {
            "id": precinct_id,
            "district_id": district_id,
            "name": name.strip() or precinct_id,
        }
    )
    save_organization(org)
    precinct_dir(precinct_id).mkdir(parents=True, exist_ok=True)
    return Precinct(id=precinct_id, district_id=district_id, name=name.strip() or precinct_id)


def suggest_next_precinct_suffix(district_id: str) -> str:
    district_id = normalize_id(district_id)
    numbers: list[int] = []
    for precinct in list_precincts(district_id):
        if precinct.id.startswith(district_id):
            tail = precinct.id[len(district_id) :]
            if tail.isdigit():
                numbers.append(int(tail))
    return f"{max(numbers, default=0) + 1:02d}"


def precinct_ids_for_district(district_id: str) -> set[str]:
    return {p.id for p in list_precincts(district_id)}


def migrate_legacy_data() -> bool:
    """Move flat data/*.csv into the default precinct folder once."""
    legacy_status = config.CSV_PATH
    target_paths = paths_for_precinct(config.DEFAULT_PRECINCT_ID)
    if not legacy_status.exists():
        return False
    if target_paths.status.exists() and target_paths.status.stat().st_size > 0:
        return False

    init_organization()
    target_paths.status.parent.mkdir(parents=True, exist_ok=True)

    moves = [
        (config.CSV_PATH, target_paths.status),
        (config.ADDRESSES_PATH, target_paths.addresses),
        (config.LAST_SYNC_PATH, target_paths.last_sync),
    ]
    migrated = False
    for src, dst in moves:
        if src.exists():
            shutil.move(str(src), str(dst))
            migrated = True
    return migrated


def init_precinct_data(precinct_id: str) -> PrecinctPaths:
    """Ensure organization, precinct folder, and CSV files exist."""
    from address_store import init_addresses
    from csv_store import init_csv

    init_organization()
    paths = paths_for_precinct(precinct_id)
    paths.status.parent.mkdir(parents=True, exist_ok=True)
    init_csv(paths.status)
    init_addresses(paths.addresses)
    return paths
