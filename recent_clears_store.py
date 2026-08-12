"""Track houses recently cleared to GREEN for heartbeat explicit-clear packets."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from config import PRECINCTS_DIR, RECENT_CLEARS_WINDOW_SECONDS

_lock = threading.Lock()


def _store_path(precinct_id: str) -> Path:
    precinct_id = precinct_id.strip().upper()
    return PRECINCTS_DIR / precinct_id / "recent_clears.json"


def record_clear(precinct_id: str, house_id: str) -> None:
    """Remember that a house was cleared to GREEN for upcoming heartbeats."""
    precinct_id = precinct_id.strip().upper()
    house_id = house_id.strip().upper()
    path = _store_path(precinct_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()

    with _lock:
        data = _read_unlocked(path)
        data[house_id] = now
        _write_unlocked(path, data)


def get_recent_clears(
    precinct_id: str,
    window_seconds: float = RECENT_CLEARS_WINDOW_SECONDS,
) -> list[str]:
    """Return house IDs cleared to GREEN within the retention window."""
    precinct_id = precinct_id.strip().upper()
    path = _store_path(precinct_id)
    cutoff = time.time() - window_seconds

    with _lock:
        data = _read_unlocked(path)
        fresh = {
            house_id: cleared_at
            for house_id, cleared_at in data.items()
            if cleared_at >= cutoff
        }
        if fresh != data:
            _write_unlocked(path, fresh)
        return sorted(fresh)


def prune_recent_clears(precinct_id: str, house_ids: list[str]) -> None:
    """Remove houses after they were included in a successful heartbeat."""
    if not house_ids:
        return

    precinct_id = precinct_id.strip().upper()
    path = _store_path(precinct_id)
    remove = {house_id.strip().upper() for house_id in house_ids}

    with _lock:
        data = _read_unlocked(path)
        for house_id in remove:
            data.pop(house_id, None)
        _write_unlocked(path, data)


def _read_unlocked(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for house_id, cleared_at in raw.items():
        try:
            result[str(house_id).upper()] = float(cleared_at)
        except (TypeError, ValueError):
            continue
    return result


def _write_unlocked(path: Path, data: dict[str, float]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
