"""Persist radio and sync settings for the Streamlit UI."""

from __future__ import annotations

import json
import threading
from typing import Any

from config import (
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
