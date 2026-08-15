"""Encode/decode compact neighborhood status packets for Meshtastic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from config import (
    MESH_MAX_PAYLOAD_BYTES,
    PACKET_PREFIX,
    STATUS_CODES,
    STATUS_FROM_WIRE,
    STATUS_GREEN,
    STATUS_WIRE,
)

# New wire format: NS:CHARC01:H001:R  (R/Y/K/G — K is BLACK; B is bulk)
_PACKET_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:([A-Z0-9]{{4,12}}):([A-Z0-9]{{1,8}}):([RYKG])$",
    re.IGNORECASE,
)

# Legacy single-house format: NS:H001:R
_LEGACY_PACKET_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:([A-Z0-9]{{1,8}}):([RYKG])$",
    re.IGNORECASE,
)

# New bulk format: NS:CHARC01:B:H001Y,H002R
_BULK_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:([A-Z0-9]{{4,12}}):B:(.+)$",
    re.IGNORECASE,
)

# Legacy bulk format: NS:B:H001Y,H002R
_LEGACY_BULK_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:B:(.+)$",
    re.IGNORECASE,
)

# Heartbeat markers: NS:SOUTH01:HB:S / NS:SOUTH01:HB:E
_HB_START_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:([A-Z0-9]{{4,12}}):HB:S$",
    re.IGNORECASE,
)
_HB_END_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:([A-Z0-9]{{4,12}}):HB:E$",
    re.IGNORECASE,
)

# Recent clears: NS:SOUTH01:C:H003G,H009G
_CLEAR_RE = re.compile(
    rf"^{re.escape(PACKET_PREFIX)}:([A-Z0-9]{{4,12}}):C:(.+)$",
    re.IGNORECASE,
)

_BULK_PART_RE = re.compile(r"^([A-Za-z0-9]{1,8})([RYKG])$", re.IGNORECASE)


@dataclass(frozen=True)
class MeshUpdate:
    precinct_id: str | None
    house_id: str
    status_code: str


class ControlPacketKind(str, Enum):
    HEARTBEAT_START = "heartbeat_start"
    HEARTBEAT_END = "heartbeat_end"
    RECENT_CLEARS = "recent_clears"


@dataclass(frozen=True)
class ControlPacket:
    kind: ControlPacketKind
    precinct_id: str
    updates: tuple[MeshUpdate, ...] = ()


def encode_status(precinct_id: str, house_id: str, status_code: str) -> str:
    """Build a short text packet for mesh transmission."""
    precinct_id = precinct_id.strip().upper()
    house_id = house_id.strip().upper()
    status_code = status_code.strip().upper()

    if status_code not in STATUS_CODES:
        raise ValueError(f"Invalid status code: {status_code}")
    if not precinct_id:
        raise ValueError("precinct_id is required")
    if not house_id:
        raise ValueError("house_id is required")

    wire_status = STATUS_WIRE[status_code]
    return f"{PACKET_PREFIX}:{precinct_id}:{house_id}:{wire_status}"


def _house_part(house_id: str, status_code: str) -> str:
    house_id = house_id.strip().upper()
    status_code = status_code.strip().upper()
    if status_code not in STATUS_CODES:
        raise ValueError(f"Invalid status code: {status_code}")
    if not house_id:
        raise ValueError("house_id is required")
    return f"{house_id}{STATUS_WIRE[status_code]}"


def _bulk_packet_for_parts(precinct_id: str, parts: list[str]) -> str:
    precinct_id = precinct_id.strip().upper()
    return f"{PACKET_PREFIX}:{precinct_id}:B:{','.join(parts)}"


def _packet_byte_len(packet: str) -> int:
    return len(packet.encode("utf-8"))


def encode_bulk_sync_chunks(
    precinct_id: str,
    rows: list[tuple[str, str]],
    max_bytes: int = MESH_MAX_PAYLOAD_BYTES,
) -> list[str]:
    """
    Encode house statuses into one or more bulk mesh packets.

    Splits across multiple NS:{precinct}:B:... packets when the full board exceeds
    max_bytes. Each chunk is independently decodable on the receiver.
    """
    if not rows:
        raise ValueError("rows must not be empty")

    precinct_id = precinct_id.strip().upper()
    if not precinct_id:
        raise ValueError("precinct_id is required")

    parts = [_house_part(house_id, status_code) for house_id, status_code in rows]
    chunks: list[list[str]] = []
    current: list[str] = []

    for part in parts:
        candidate = current + [part]
        packet = _bulk_packet_for_parts(precinct_id, candidate)
        if _packet_byte_len(packet) <= max_bytes:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = [part]
            packet = _bulk_packet_for_parts(precinct_id, current)
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

    return [_bulk_packet_for_parts(precinct_id, chunk) for chunk in chunks]


def encode_bulk_sync(precinct_id: str, rows: list[tuple[str, str]]) -> str:
    """
    Encode all house statuses into a single bulk mesh packet.

    Raises ValueError if the board is too large; use encode_bulk_sync_chunks().
    """
    chunks = encode_bulk_sync_chunks(precinct_id, rows)
    if len(chunks) > 1:
        raise ValueError(
            f"Bulk sync requires {len(chunks)} packets; use encode_bulk_sync_chunks()"
        )
    return chunks[0]


def decode_updates(text: str) -> list[MeshUpdate]:
    """
    Parse a mesh message into zero or more neighborhood status updates.

    Supports precinct-tagged and legacy packet formats.
    """
    if not text:
        return []

    text = text.strip()

    bulk_match = _BULK_RE.match(text)
    if bulk_match:
        precinct_id = bulk_match.group(1).upper()
        return _parse_bulk_parts(precinct_id, bulk_match.group(2))

    legacy_bulk_match = _LEGACY_BULK_RE.match(text)
    if legacy_bulk_match:
        return _parse_bulk_parts(None, legacy_bulk_match.group(1))

    single = decode_packet(text)
    return [single] if single else []


def is_status_packet(text: str) -> bool:
    """Return True when a mesh message is a neighborhood status packet."""
    return bool(decode_updates(text))


def _parse_bulk_parts(precinct_id: str | None, payload: str) -> list[MeshUpdate]:
    updates: list[MeshUpdate] = []
    for part in payload.split(","):
        part = part.strip()
        if not part:
            continue
        part_match = _BULK_PART_RE.match(part)
        if part_match is None:
            continue
        house_id = part_match.group(1).upper()
        status_code = STATUS_FROM_WIRE.get(part_match.group(2).upper())
        if status_code is not None:
            updates.append(
                MeshUpdate(
                    precinct_id=precinct_id,
                    house_id=house_id,
                    status_code=status_code,
                )
            )
    return updates


def decode_packet(text: str) -> MeshUpdate | None:
    """
    Parse an incoming single-house mesh text message.

    Returns MeshUpdate or None if not a neighborhood packet.
    """
    if not text:
        return None

    text = text.strip()
    match = _PACKET_RE.match(text)
    if match:
        precinct_id = match.group(1).upper()
        house_id = match.group(2).upper()
        wire_status = match.group(3).upper()
        status_code = STATUS_FROM_WIRE.get(wire_status)
        if status_code is None:
            return None
        return MeshUpdate(
            precinct_id=precinct_id,
            house_id=house_id,
            status_code=status_code,
        )

    legacy_match = _LEGACY_PACKET_RE.match(text)
    if legacy_match:
        house_id = legacy_match.group(1).upper()
        wire_status = legacy_match.group(2).upper()
        status_code = STATUS_FROM_WIRE.get(wire_status)
        if status_code is None:
            return None
        return MeshUpdate(
            precinct_id=None,
            house_id=house_id,
            status_code=status_code,
        )

    return None


def encode_bulk(precinct_id: str, rows: list[tuple[str, str]]) -> list[str]:
    """Encode multiple house statuses as individual packets."""
    return [
        encode_status(precinct_id, house_id, status_code)
        for house_id, status_code in rows
    ]


def encode_heartbeat_start(precinct_id: str) -> str:
    precinct_id = precinct_id.strip().upper()
    return f"{PACKET_PREFIX}:{precinct_id}:HB:S"


def encode_heartbeat_end(precinct_id: str) -> str:
    precinct_id = precinct_id.strip().upper()
    return f"{PACKET_PREFIX}:{precinct_id}:HB:E"


def _clears_packet_for_parts(precinct_id: str, parts: list[str]) -> str:
    precinct_id = precinct_id.strip().upper()
    return f"{PACKET_PREFIX}:{precinct_id}:C:{','.join(parts)}"


def encode_recent_clear_chunks(
    precinct_id: str,
    rows: list[tuple[str, str]],
    max_bytes: int = MESH_MAX_PAYLOAD_BYTES,
) -> list[str]:
    """Encode explicit GREEN clears into NS:{precinct}:C:... packets."""
    if not rows:
        return []

    precinct_id = precinct_id.strip().upper()
    parts = [_house_part(house_id, status_code) for house_id, status_code in rows]
    for house_id, status_code in rows:
        if status_code.upper() != STATUS_GREEN:
            raise ValueError(
                f"Recent clear packets only carry GREEN statuses (got {house_id}={status_code})"
            )

    chunks: list[list[str]] = []
    current: list[str] = []

    for part in parts:
        candidate = current + [part]
        packet = _clears_packet_for_parts(precinct_id, candidate)
        if _packet_byte_len(packet) <= max_bytes:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = [part]
            packet = _clears_packet_for_parts(precinct_id, current)
            if _packet_byte_len(packet) > max_bytes:
                raise ValueError(
                    f"Clear entry {part!r} exceeds packet limit ({max_bytes} bytes)"
                )
        else:
            raise ValueError(
                f"Clear entry {part!r} exceeds packet limit ({max_bytes} bytes)"
            )

    if current:
        chunks.append(current)

    return [_clears_packet_for_parts(precinct_id, chunk) for chunk in chunks]


def build_heartbeat_packets(
    precinct_id: str,
    non_green_rows: list[tuple[str, str]],
    recent_clear_rows: list[tuple[str, str]],
    max_bytes: int = MESH_MAX_PAYLOAD_BYTES,
) -> list[str]:
    """Build a full heartbeat sequence for one precinct."""
    packets = [encode_heartbeat_start(precinct_id)]
    if non_green_rows:
        packets.extend(
            encode_bulk_sync_chunks(precinct_id, non_green_rows, max_bytes=max_bytes)
        )
    if recent_clear_rows:
        packets.extend(
            encode_recent_clear_chunks(precinct_id, recent_clear_rows, max_bytes=max_bytes)
        )
    packets.append(encode_heartbeat_end(precinct_id))
    return packets


def parse_control_packet(text: str) -> ControlPacket | None:
    """Parse heartbeat markers and recent-clear packets."""
    if not text:
        return None

    text = text.strip()
    start_match = _HB_START_RE.match(text)
    if start_match:
        return ControlPacket(
            kind=ControlPacketKind.HEARTBEAT_START,
            precinct_id=start_match.group(1).upper(),
        )

    end_match = _HB_END_RE.match(text)
    if end_match:
        return ControlPacket(
            kind=ControlPacketKind.HEARTBEAT_END,
            precinct_id=end_match.group(1).upper(),
        )

    clear_match = _CLEAR_RE.match(text)
    if clear_match:
        precinct_id = clear_match.group(1).upper()
        updates = tuple(_parse_bulk_parts(precinct_id, clear_match.group(2)))
        return ControlPacket(
            kind=ControlPacketKind.RECENT_CLEARS,
            precinct_id=precinct_id,
            updates=updates,
        )

    return None


def is_control_packet(text: str) -> bool:
    return parse_control_packet(text) is not None


def is_mesh_protocol_packet(text: str) -> bool:
    """Return True for any Block Status mesh protocol message (not free-form text)."""
    return is_status_packet(text) or is_control_packet(text)
