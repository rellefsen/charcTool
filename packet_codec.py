"""Encode/decode compact neighborhood status packets for Meshtastic."""

from __future__ import annotations

import re

from config import (
    MESH_MAX_PAYLOAD_BYTES,
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

# Bulk sync format: NS:B:H001Y,H002R,H003G
_BULK_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:B:(.+)$",
    re.IGNORECASE,
)
_BULK_PART_RE = re.compile(r"^([A-Za-z0-9]{1,8})([RYG])$", re.IGNORECASE)


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


def _house_part(house_id: str, status_code: str) -> str:
    house_id = house_id.strip().upper()
    status_code = status_code.strip().upper()
    if status_code not in STATUS_CODES:
        raise ValueError(f"Invalid status code: {status_code}")
    if not house_id:
        raise ValueError("house_id is required")
    return f"{house_id}{STATUS_WIRE[status_code]}"


def _bulk_packet_for_parts(parts: list[str]) -> str:
    return f"{PACKET_PREFIX}:B:{','.join(parts)}"


def _packet_byte_len(packet: str) -> int:
    return len(packet.encode("utf-8"))


def encode_bulk_sync_chunks(
    rows: list[tuple[str, str]],
    max_bytes: int = MESH_MAX_PAYLOAD_BYTES,
) -> list[str]:
    """
    Encode house statuses into one or more bulk mesh packets.

    Splits across multiple NS:B:... packets when the full board exceeds
    max_bytes. Each chunk is independently decodable on the receiver.
    """
    if not rows:
        raise ValueError("rows must not be empty")

    parts = [_house_part(house_id, status_code) for house_id, status_code in rows]
    chunks: list[list[str]] = []
    current: list[str] = []

    for part in parts:
        candidate = current + [part]
        packet = _bulk_packet_for_parts(candidate)
        if _packet_byte_len(packet) <= max_bytes:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = [part]
            packet = _bulk_packet_for_parts(current)
            if _packet_byte_len(packet) > max_bytes:
                raise ValueError(
                    f"House entry {part!r} exceeds packet limit ({max_bytes} bytes)"
                )
        else:
            raise ValueError(
                f"House entry {part!r} exceeds packet limit ({max_bytes} bytes)"
            )

    if current:
        chunks.append(current)

    return [_bulk_packet_for_parts(chunk) for chunk in chunks]


def encode_bulk_sync(rows: list[tuple[str, str]]) -> str:
    """
    Encode all house statuses into a single bulk mesh packet.

    Raises ValueError if the board is too large; use encode_bulk_sync_chunks().
    """
    chunks = encode_bulk_sync_chunks(rows)
    if len(chunks) > 1:
        raise ValueError(
            f"Bulk sync requires {len(chunks)} packets; use encode_bulk_sync_chunks()"
        )
    return chunks[0]


def decode_updates(text: str) -> list[tuple[str, str]]:
    """
    Parse a mesh message into zero or more (house_id, status_code) updates.

    Supports single-packet (NS:H001:R) and bulk-sync (NS:B:H001Y,H002R) formats.
    """
    if not text:
        return []

    text = text.strip()
    bulk_match = _BULK_RE.match(text)
    if bulk_match:
        updates: list[tuple[str, str]] = []
        for part in bulk_match.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            part_match = _BULK_PART_RE.match(part)
            if part_match is None:
                continue
            house_id = part_match.group(1).upper()
            status_code = STATUS_FROM_WIRE.get(part_match.group(2).upper())
            if status_code is not None:
                updates.append((house_id, status_code))
        return updates

    single = decode_packet(text)
    return [single] if single else []


def decode_packet(text: str) -> tuple[str, str] | None:
    """
    Parse an incoming single-house mesh text message.

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
