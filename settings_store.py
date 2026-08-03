"""Persist radio and sync settings for the Streamlit UI."""

from __future__ import annotations

import json
import threading
from typing import Any

from config import (
    DEFAULT_DISTRICT_ID,
    DEFAULT_PRECINCT_ID,
    EXPORT_ACK_TIMEOUT,
    EXPORT_ACK_WINDOW,
    EXPORT_MAX_RETRIES,
    EXPORT_MIN_PACKET_DELAY,
    EXPORT_PACKET_DELAY,
    MESHTASTIC_CHANNEL_NAME,
    MESHTASTIC_PORT,
    SYNC_PACKET_DELAY,
)
import config

_lock = threading.Lock()

DEFAULT_SETTINGS: dict[str, Any] = {
    "meshtastic_port": MESHTASTIC_PORT,
    "channel_name": MESHTASTIC_CHANNEL_NAME,
    "sync_packet_delay": SYNC_PACKET_DELAY,
    "export_packet_delay": EXPORT_PACKET_DELAY,
    "export_ack_timeout": EXPORT_ACK_TIMEOUT,
    "export_max_retries": EXPORT_MAX_RETRIES,
    "export_ack_window": EXPORT_ACK_WINDOW,
    "export_min_packet_delay": EXPORT_MIN_PACKET_DELAY,
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

    channel = raw.get("channel_name", settings["channel_name"])
    settings["channel_name"] = str(channel).strip()

    delay = raw.get("sync_packet_delay", settings["sync_packet_delay"])
    try:
        settings["sync_packet_delay"] = float(delay)
    except (TypeError, ValueError) as exc:
        raise SettingsError("Sync delay must be a number.") from exc

    export_delay = raw.get("export_packet_delay", settings["export_packet_delay"])
    try:
        settings["export_packet_delay"] = float(export_delay)
    except (TypeError, ValueError) as exc:
        raise SettingsError("Export delay must be a number.") from exc

    ack_timeout = raw.get("export_ack_timeout", settings["export_ack_timeout"])
    try:
        settings["export_ack_timeout"] = float(ack_timeout)
    except (TypeError, ValueError) as exc:
        raise SettingsError("Export ACK timeout must be a number.") from exc

    try:
        settings["export_max_retries"] = int(raw.get("export_max_retries", settings["export_max_retries"]))
    except (TypeError, ValueError) as exc:
        raise SettingsError("Export max retries must be a whole number.") from exc

    try:
        settings["export_ack_window"] = int(raw.get("export_ack_window", settings["export_ack_window"]))
    except (TypeError, ValueError) as exc:
        raise SettingsError("Export ACK window must be a whole number.") from exc

    export_min_delay = raw.get("export_min_packet_delay", settings["export_min_packet_delay"])
    try:
        settings["export_min_packet_delay"] = float(export_min_delay)
    except (TypeError, ValueError) as exc:
        raise SettingsError("Export minimum delay must be a number.") from exc

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

    export_delay = float(settings["export_packet_delay"])
    if export_delay < 0:
        raise SettingsError("Export delay cannot be negative.")
    if export_delay > 60:
        raise SettingsError("Export delay must be 60 seconds or less.")

    ack_timeout = float(settings["export_ack_timeout"])
    if ack_timeout < 1:
        raise SettingsError("Export ACK timeout must be at least 1 second.")
    if ack_timeout > 120:
        raise SettingsError("Export ACK timeout must be 120 seconds or less.")

    retries = int(settings["export_max_retries"])
    if retries < 1:
        raise SettingsError("Export max retries must be at least 1.")
    if retries > 10:
        raise SettingsError("Export max retries must be 10 or less.")

    ack_window = int(settings["export_ack_window"])
    if ack_window < 1:
        raise SettingsError("Export ACK window must be at least 1.")
    if ack_window > 20:
        raise SettingsError("Export ACK window must be 20 or less.")

    min_delay = float(settings["export_min_packet_delay"])
    if min_delay < 0:
        raise SettingsError("Export minimum delay cannot be negative.")
    if min_delay > export_delay:
        raise SettingsError("Export minimum delay cannot exceed export delay.")


def load_settings() -> dict[str, Any]:
    """Load saved settings, falling back to config defaults."""
    target = config.SETTINGS_PATH
    with _lock:
        if not target.exists():
            return DEFAULT_SETTINGS.copy()

        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return DEFAULT_SETTINGS.copy()

        if not isinstance(raw, dict):
            return DEFAULT_SETTINGS.copy()

        try:
            return _coerce_settings(raw)
        except SettingsError:
            return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist settings to disk."""
    normalized = _coerce_settings(settings)
    target = config.SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        target.write_text(
            json.dumps(normalized, indent=2) + "\n",
            encoding="utf-8",
        )

    return normalized
