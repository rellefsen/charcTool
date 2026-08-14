"""Persist radio and sync settings for the Streamlit UI."""

from __future__ import annotations

import json
import threading
from typing import Any

from config import (
    CONNECTION_SERIAL,
    CONNECTION_TYPES,
    DEFAULT_DISTRICT_ID,
    DEFAULT_PRECINCT_ID,
    HEARTBEAT_INTERVAL_SECONDS,
    MESHTASTIC_BLE_ADDRESS,
    MESHTASTIC_CHANNEL_NAME,
    MESHTASTIC_CONNECTION_TYPE,
    MESHTASTIC_PORT,
    SYNC_PACKET_DELAY,
)
import config

_lock = threading.Lock()

DEFAULT_SETTINGS: dict[str, Any] = {
    "meshtastic_port": MESHTASTIC_PORT,
    "connection_type": MESHTASTIC_CONNECTION_TYPE,
    "ble_address": MESHTASTIC_BLE_ADDRESS,
    "channel_name": MESHTASTIC_CHANNEL_NAME,
    "sync_packet_delay": SYNC_PACKET_DELAY,
    "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
    "heartbeat_enabled": True,
    "active_precinct_id": DEFAULT_PRECINCT_ID,
    "active_district_id": DEFAULT_DISTRICT_ID,
    "show_mock_testing": True,
}


class SettingsError(ValueError):
    """Raised when settings fail validation."""


def _coerce_settings(raw: dict[str, Any]) -> dict[str, Any]:
    settings = DEFAULT_SETTINGS.copy()

    port = raw.get("meshtastic_port")
    if port is None or port == "":
        settings["meshtastic_port"] = None
    elif isinstance(port, str):
        settings["meshtastic_port"] = port.strip() or None
    else:
        settings["meshtastic_port"] = str(port)

    connection_type = str(
        raw.get("connection_type", settings["connection_type"])
    ).strip().lower()
    settings["connection_type"] = (
        connection_type if connection_type in CONNECTION_TYPES else CONNECTION_SERIAL
    )

    ble_address = raw.get("ble_address", settings["ble_address"])
    if ble_address is None or ble_address == "":
        settings["ble_address"] = None
    else:
        settings["ble_address"] = str(ble_address).strip() or None

    channel = raw.get("channel_name", settings["channel_name"])
    settings["channel_name"] = str(channel).strip()

    delay = raw.get("sync_packet_delay", settings["sync_packet_delay"])
    try:
        settings["sync_packet_delay"] = float(delay)
    except (TypeError, ValueError) as exc:
        raise SettingsError("Sync delay must be a number.") from exc

    heartbeat_interval = raw.get(
        "heartbeat_interval_seconds",
        settings["heartbeat_interval_seconds"],
    )
    try:
        settings["heartbeat_interval_seconds"] = float(heartbeat_interval)
    except (TypeError, ValueError) as exc:
        raise SettingsError("Heartbeat interval must be a number.") from exc

    settings["heartbeat_enabled"] = bool(
        raw.get("heartbeat_enabled", settings["heartbeat_enabled"])
    )

    precinct = str(raw.get("active_precinct_id", settings["active_precinct_id"])).strip().upper()
    district = str(raw.get("active_district_id", settings["active_district_id"])).strip().upper()
    settings["active_precinct_id"] = precinct or DEFAULT_PRECINCT_ID
    settings["active_district_id"] = district or DEFAULT_DISTRICT_ID
    settings["show_mock_testing"] = bool(raw.get("show_mock_testing", settings["show_mock_testing"]))

    validate_settings(settings)
    return settings


def validate_settings(settings: dict[str, Any]) -> None:
    channel = str(settings.get("channel_name", "")).strip()
    if not channel:
        raise SettingsError("Mesh channel name is required.")

    delay = float(settings["sync_packet_delay"])
    if delay < 0:
        raise SettingsError("Sync delay cannot be negative.")
    if delay > 30:
        raise SettingsError("Sync delay must be 30 seconds or less.")

    heartbeat_interval = float(settings["heartbeat_interval_seconds"])
    if heartbeat_interval < 60:
        raise SettingsError("Heartbeat interval must be at least 60 seconds.")
    if heartbeat_interval > 86400:
        raise SettingsError("Heartbeat interval must be 24 hours or less.")


def load_settings() -> dict[str, Any]:
    """Load saved settings, falling back to config defaults."""
    target = config.SETTINGS_PATH
    with _lock:
        if not target.exists():
            return DEFAULT_SETTINGS.copy()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SettingsError(f"Could not read settings from {target}") from exc

    return _coerce_settings(raw)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist settings to disk."""
    validated = _coerce_settings(settings)
    target = config.SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        target.write_text(json.dumps(validated, indent=2, sort_keys=True), encoding="utf-8")

    return validated
