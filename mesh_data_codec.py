"""Encode/decode mesh packets for full organization and address export."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import Enum

from config import (
    DATA_PACKET_PREFIX,
    EXPORT_ACK_TIMEOUT,
    EXPORT_ACK_WINDOW,
    EXPORT_MIN_PACKET_DELAY,
    EXPORT_PACKET_DELAY,
    MESH_MAX_PAYLOAD_BYTES,
)
from csv_store import read_all
from packet_codec import encode_bulk_sync_chunks

_NUMBERED_EXPORT_RE = re.compile(
    r"^(ND|NS):(\d+)/(\d+):(.+)$",
    re.IGNORECASE,
)
_DATA_DISTRICT_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:D:([A-Z0-9]{{2,8}})\|(.+)$",
    re.IGNORECASE,
)
_DATA_PRECINCT_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:P:([A-Z0-9]{{4,12}})\|([A-Z0-9]{{2,8}})\|(.+)$",
    re.IGNORECASE,
)
_DATA_ADDRESS_BULK_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:A:([A-Z0-9]{{4,12}}):B:(.+)$",
    re.IGNORECASE,
)
_DATA_START_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:S:FULL(?::(\d+))?(?::(\d+))?$",
    re.IGNORECASE,
)
_DATA_END_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:Z:FULL$",
    re.IGNORECASE,
)
_DATA_INDEX_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:I:(\d+)/(\d+)$",
    re.IGNORECASE,
)
_DATA_ACK_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:K:(\d+)/(\d+)$",
    re.IGNORECASE,
)
_ADDRESS_PART_RE = re.compile(r"^([A-Za-z0-9]{1,8})\|(.+)$")


class MeshDataKind(str, Enum):
    START = "start"
    END = "end"
    INDEX = "index"
    ACK = "ack"
    DISTRICT = "district"
    PRECINCT = "precinct"
    ADDRESSES = "addresses"


@dataclass(frozen=True)
class MeshAddressEntry:
    house_id: str
    address: str


@dataclass(frozen=True)
class MeshDataPacket:
    kind: MeshDataKind
    district_id: str | None = None
    district_name: str | None = None
    precinct_id: str | None = None
    precinct_name: str | None = None
    addresses: tuple[MeshAddressEntry, ...] = ()
    seq: int | None = None
    total: int | None = None
    ack_window: int | None = None


def _sanitize_field(value: str) -> str:
    return value.replace("|", " ").replace(",", " ").strip()


def _packet_byte_len(packet: str) -> int:
    return len(packet.encode("utf-8"))


def encode_export_start(
    total_payloads: int | None = None,
    ack_window: int | None = None,
) -> str:
    if total_payloads is None:
        return f"{DATA_PACKET_PREFIX}:S:FULL"
    start = f"{DATA_PACKET_PREFIX}:S:FULL:{int(total_payloads)}"
    if ack_window is not None:
        start = f"{start}:{int(ack_window)}"
    return start


def encode_export_end() -> str:
    return f"{DATA_PACKET_PREFIX}:Z:FULL"


def encode_export_index(seq: int, total: int) -> str:
    """Legacy index marker kept for backward compatibility with older transmitters."""
    return f"{DATA_PACKET_PREFIX}:I:{int(seq)}/{int(total)}"


def encode_numbered_export_packet(seq: int, total: int, packet: str) -> str:
    """Prefix a payload with its sequence number (ND:3/24:D:... or NS:3/24:...)."""
    prefix, _, rest = packet.partition(":")
    prefix = prefix.upper()
    if prefix not in {"ND", "NS"}:
        raise ValueError(f"Cannot number export packet with prefix {prefix!r}")
    return f"{prefix}:{int(seq)}/{int(total)}:{rest}"


def parse_numbered_export_packet(text: str) -> tuple[int, int, str] | None:
    """Return (seq, total, body) when text embeds an export sequence prefix."""
    match = _NUMBERED_EXPORT_RE.match(text.strip())
    if not match:
        return None
    prefix = match.group(1).upper()
    return (
        int(match.group(2)),
        int(match.group(3)),
        f"{prefix}:{match.group(4)}",
    )


def estimate_export_seconds(
    total_payloads: int,
    *,
    delay_seconds: float = EXPORT_PACKET_DELAY,
    min_delay_seconds: float = EXPORT_MIN_PACKET_DELAY,
    ack_timeout_seconds: float = EXPORT_ACK_TIMEOUT,
    ack_window: int = EXPORT_ACK_WINDOW,
) -> float:
    """Rough export duration estimate for UI display."""
    if total_payloads <= 0:
        return 0.0
    window = max(1, ack_window)
    windows = math.ceil(total_payloads / window)
    per_window = max(min_delay_seconds, 0.0) * max(window - 1, 0)
    per_window += ack_timeout_seconds * 0.35
    return windows * per_window + delay_seconds


def encode_export_ack(seq: int, total: int) -> str:
    return f"{DATA_PACKET_PREFIX}:K:{int(seq)}/{int(total)}"


def parse_export_ack(text: str) -> tuple[int, int] | None:
    match = _DATA_ACK_RE.match(text.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def encode_district(district_id: str, name: str) -> str:
    district_id = district_id.strip().upper()
    district_name = _sanitize_field(name) or district_id
    return f"{DATA_PACKET_PREFIX}:D:{district_id}|{district_name}"


def encode_precinct(precinct_id: str, district_id: str, name: str) -> str:
    precinct_id = precinct_id.strip().upper()
    district_id = district_id.strip().upper()
    precinct_name = _sanitize_field(name) or precinct_id
    return f"{DATA_PACKET_PREFIX}:P:{precinct_id}|{district_id}|{precinct_name}"


def _address_part(house_id: str, address: str) -> str:
    house_id = house_id.strip().upper()
    cleaned_address = _sanitize_field(address)
    return f"{house_id}|{cleaned_address}"


def _address_bulk_packet(precinct_id: str, parts: list[str]) -> str:
    precinct_id = precinct_id.strip().upper()
    return f"{DATA_PACKET_PREFIX}:A:{precinct_id}:B:{','.join(parts)}"


def encode_address_chunks(
    precinct_id: str,
    rows: list[tuple[str, str]],
    max_bytes: int = MESH_MAX_PAYLOAD_BYTES,
) -> list[str]:
    """Encode house addresses into one or more ND:A packets."""
    if not rows:
        return []

    precinct_id = precinct_id.strip().upper()
    parts = [_address_part(house_id, address) for house_id, address in rows]
    chunks: list[list[str]] = []
    current: list[str] = []

    for part in parts:
        candidate = current + [part]
        packet = _address_bulk_packet(precinct_id, candidate)
        if _packet_byte_len(packet) <= max_bytes:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = [part]
            packet = _address_bulk_packet(precinct_id, current)
            if _packet_byte_len(packet) > max_bytes:
                raise ValueError(
                    f"Address entry {part!r} exceeds packet limit ({max_bytes} bytes)"
                )
        else:
            raise ValueError(
                f"Address entry {part!r} exceeds packet limit ({max_bytes} bytes)"
            )

    if current:
        chunks.append(current)

    return [_address_bulk_packet(precinct_id, chunk) for chunk in chunks]


def decode_mesh_data(text: str) -> MeshDataPacket | None:
    """Parse a mesh data export packet, or return None if not recognized."""
    if not text:
        return None

    text = text.strip()
    seq: int | None = None
    total: int | None = None
    numbered = parse_numbered_export_packet(text)
    if numbered is not None:
        seq, total, text = numbered

    upper = text.upper()

    start_match = _DATA_START_RE.match(upper)
    if start_match:
        start_total = int(start_match.group(1)) if start_match.group(1) else None
        ack_window = int(start_match.group(2)) if start_match.group(2) else None
        return MeshDataPacket(
            kind=MeshDataKind.START,
            total=start_total,
            ack_window=ack_window,
        )

    if _DATA_END_RE.match(upper):
        return MeshDataPacket(kind=MeshDataKind.END)

    index_match = _DATA_INDEX_RE.match(upper)
    if index_match:
        return MeshDataPacket(
            kind=MeshDataKind.INDEX,
            seq=int(index_match.group(1)),
            total=int(index_match.group(2)),
        )

    ack_match = _DATA_ACK_RE.match(upper)
    if ack_match:
        return MeshDataPacket(
            kind=MeshDataKind.ACK,
            seq=int(ack_match.group(1)),
            total=int(ack_match.group(2)),
        )

    district_match = _DATA_DISTRICT_RE.match(text)
    if district_match:
        packet = MeshDataPacket(
            kind=MeshDataKind.DISTRICT,
            district_id=district_match.group(1).upper(),
            district_name=district_match.group(2).strip(),
        )
        if seq is not None:
            packet = replace(packet, seq=seq, total=total)
        return packet

    precinct_match = _DATA_PRECINCT_RE.match(text)
    if precinct_match:
        packet = MeshDataPacket(
            kind=MeshDataKind.PRECINCT,
            precinct_id=precinct_match.group(1).upper(),
            district_id=precinct_match.group(2).upper(),
            precinct_name=precinct_match.group(3).strip(),
        )
        if seq is not None:
            packet = replace(packet, seq=seq, total=total)
        return packet

    address_match = _DATA_ADDRESS_BULK_RE.match(text)
    if address_match:
        precinct_id = address_match.group(1).upper()
        entries: list[MeshAddressEntry] = []
        for part in address_match.group(2).split(","):
            part = part.strip()
            if not part:
                continue
            part_match = _ADDRESS_PART_RE.match(part)
            if part_match is None:
                continue
            entries.append(
                MeshAddressEntry(
                    house_id=part_match.group(1).upper(),
                    address=part_match.group(2).strip(),
                )
            )
        packet = MeshDataPacket(
            kind=MeshDataKind.ADDRESSES,
            precinct_id=precinct_id,
            addresses=tuple(entries),
        )
        if seq is not None:
            packet = replace(packet, seq=seq, total=total)
        return packet

    return None


def is_data_packet(text: str) -> bool:
    """Return True when a mesh message is an organization/address export packet."""
    return decode_mesh_data(text) is not None


def build_export_payloads(
    *,
    district_ids: set[str] | None = None,
    precinct_ids: set[str] | None = None,
) -> list[str]:
    """Build export payloads for all data or a selected scope."""
    from address_store import read_address_map
    from precinct_store import get_precinct, list_districts, list_precincts, paths_for_precinct

    selected_districts: set[str] = set()
    selected_precincts: list = []

    if precinct_ids:
        for precinct_id in sorted({pid.strip().upper() for pid in precinct_ids if pid.strip()}):
            precinct = get_precinct(precinct_id)
            if precinct is None:
                raise ValueError(f"Unknown precinct: {precinct_id}")
            selected_precincts.append(precinct)
            selected_districts.add(precinct.district_id)
    elif district_ids:
        selected_districts = {did.strip().upper() for did in district_ids if did.strip()}
        if not selected_districts:
            raise ValueError("Select at least one district to export.")
        for district_id in sorted(selected_districts):
            selected_precincts.extend(list_precincts(district_id))
    else:
        selected_districts = {district.id for district in list_districts()}
        selected_precincts = list_precincts()

    packets: list[str] = []
    district_lookup = {district.id: district for district in list_districts()}

    for district_id in sorted(selected_districts):
        district = district_lookup.get(district_id)
        if district is None:
            raise ValueError(f"Unknown district: {district_id}")
        packets.append(encode_district(district.id, district.name))

    for precinct in sorted(selected_precincts, key=lambda item: item.id):
        packets.append(
            encode_precinct(precinct.id, precinct.district_id, precinct.name)
        )

        paths = paths_for_precinct(precinct.id)
        address_map = read_address_map(paths.addresses)
        if address_map:
            address_rows = sorted(address_map.items())
            packets.extend(encode_address_chunks(precinct.id, address_rows))

        status_rows = read_all(paths.status)
        if status_rows:
            sync_rows = [(row["house_id"], row["status_code"]) for row in status_rows]
            packets.extend(encode_bulk_sync_chunks(precinct.id, sync_rows))

    return packets


def build_full_export_payloads() -> list[str]:
    """Build numbered export payloads (districts, precincts, addresses, statuses)."""
    return build_export_payloads()


def build_full_export_packets() -> list[str]:
    """Legacy packet list including start/end markers without index/ACK framing."""
    payloads = build_full_export_payloads()
    return [encode_export_start(), *payloads, encode_export_end()]
