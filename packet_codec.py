"""Encode/decode compact neighborhood status packets for Meshtastic."""

from __future__ import annotations

import re

from config import (
    PACKET_PREFIX,
    STATUS_CODES,
    STATUS_FROM_WIRE,
    STATUS_WIRE,
)

# Wire format: NS:H001:R  (prefix:house_id:status_letter)
_PACKET_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:([A-Za-z0-9]{{1,8}}):([RYG])$",
    re.IGNORECASE,
)


def encode_status(house_id: str, status_code: str) -> str:
    """Build a short text packet for mesh transmission."""
    house_id = house_id.strip().upper()
    status_code = status_code.strip().upper()

    if status_code not in STATUS_CODES:
        raise ValueError(f"Invalid status code: {status_code}")
    if not house_id:
        raise ValueError("house_id is required")

    wire_status = STATUS_WIRE[status_code]
    return f"{PACKET_PREFIX}:{house_id}:{wire_status}"


def decode_packet(text: str) -> tuple[str, str] | None:
    """
    Parse an incoming mesh text message.

    Returns (house_id, status_code) or None if not a neighborhood packet.
    """
    if not text:
        return None

    text = text.strip()
    match = _PACKET_RE.match(text)
    if not match:
        return None

    house_id = match.group(1).upper()
    wire_status = match.group(2).upper()
    status_code = STATUS_FROM_WIRE.get(wire_status)
    if status_code is None:
        return None

    return house_id, status_code


def encode_bulk(rows: list[tuple[str, str]]) -> list[str]:
    """Encode multiple house statuses as individual packets."""
    return [encode_status(house_id, status) for house_id, status in rows]
