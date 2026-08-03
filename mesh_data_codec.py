"""Encode/decode mesh packets for full organization and address export."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from config import DATA_PACKET_PREFIX, MESH_MAX_PAYLOAD_BYTES
from csv_store import read_all
from packet_codec import encode_bulk_sync_chunks

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
    rf"^{re.escape(DATA_PACKET_PREFIX)}:S:FULL$",
    re.IGNORECASE,
)
_DATA_END_RE = re.compile(
    rf"^{re.escape(DATA_PACKET_PREFIX)}:Z:FULL$",
    re.IGNORECASE,
)
_ADDRESS_PART_RE = re.compile(r"^([A-Za-z0-9]{1,8})\|(.+)$")


class MeshDataKind(str, Enum):
    START = "start"
    END = "end"
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


def _sanitize_field(value: str) -> str:
    return value.replace("|", " ").replace(",", " ").strip()


def _packet_byte_len(packet: str) -> int:
    return len(packet.encode("utf-8"))


def encode_export_start() -> str:
    return f"{DATA_PACKET_PREFIX}:S:FULL"


def encode_export_end() -> str:
    return f"{DATA_PACKET_PREFIX}:Z:FULL"


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
    upper = text.upper()

    if _DATA_START_RE.match(upper):
        return MeshDataPacket(kind=MeshDataKind.START)

    if _DATA_END_RE.match(upper):
        return MeshDataPacket(kind=MeshDataKind.END)

    district_match = _DATA_DISTRICT_RE.match(text)
    if district_match:
        return MeshDataPacket(
            kind=MeshDataKind.DISTRICT,
            district_id=district_match.group(1).upper(),
            district_name=district_match.group(2).strip(),
        )

    precinct_match = _DATA_PRECINCT_RE.match(text)
    if precinct_match:
        return MeshDataPacket(
            kind=MeshDataKind.PRECINCT,
            precinct_id=precinct_match.group(1).upper(),
            district_id=precinct_match.group(2).upper(),
            precinct_name=precinct_match.group(3).strip(),
        )

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
        return MeshDataPacket(
            kind=MeshDataKind.ADDRESSES,
            precinct_id=precinct_id,
            addresses=tuple(entries),
        )

    return None


def is_data_packet(text: str) -> bool:
    """Return True when a mesh message is an organization/address export packet."""
    return decode_mesh_data(text) is not None


def build_full_export_packets() -> list[str]:
    """
    Build the full mesh export sequence: org structure, addresses, and statuses.

    Packet order:
      1. ND:S:FULL start marker
      2. All districts (ND:D)
      3. All precincts (ND:P)
      4. All addresses per precinct (ND:A, chunked)
      5. All house statuses per precinct (NS:B, chunked)
      6. ND:Z:FULL end marker
    """
    from address_store import read_address_map
    from precinct_store import list_districts, list_precincts, paths_for_precinct

    packets: list[str] = [encode_export_start()]

    for district in list_districts():
        packets.append(encode_district(district.id, district.name))

    for precinct in list_precincts():
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

    packets.append(encode_export_end())
    return packets
