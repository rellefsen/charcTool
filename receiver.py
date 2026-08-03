"""Background mesh listener that updates the local CSV from incoming packets."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from address_store import apply_remote_addresses
from config import EXPORT_ACK_WINDOW, IMPORT_GRACE_SECONDS, RECEIVER_POLL_INTERVAL
from csv_store import apply_remote_update, ensure_status_csv
from mesh_data_codec import (
    MeshDataKind,
    MeshDataPacket,
    decode_mesh_data,
    parse_numbered_export_packet,
)
from meshtastic_client import MeshtasticClient
from packet_codec import decode_updates
from precinct_store import ensure_precinct_from_import, paths_for_precinct, upsert_district, upsert_precinct

logger = logging.getLogger(__name__)

RECENT_ACTIVITY_LIMIT = 15


@dataclass
class RecentActivity:
    at: str
    packet: str
    summary: str


@dataclass
class ReceiverStats:
    running: bool = False
    packets_received: int = 0
    updates_applied: int = 0
    data_imports_applied: int = 0
    import_mode: bool = False
    import_grace_until: float = 0.0
    import_complete_pending: bool = False
    last_packet: str = ""
    last_update: str = ""
    last_error: str = ""
    last_activity_at: str = ""
    recent_activity: deque = field(default_factory=lambda: deque(maxlen=RECENT_ACTIVITY_LIMIT))


@dataclass
class MeshReceiver:
    """Runs a daemon thread to process incoming neighborhood status packets."""

    client: MeshtasticClient
    poll_interval: float = RECEIVER_POLL_INTERVAL
    watched_precinct_ids: set[str] = field(default_factory=set)
    legacy_precinct_id: str | None = None
    _thread: threading.Thread | None = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    stats: ReceiverStats = field(default_factory=ReceiverStats)
    _import_seq: int | None = field(default=None, init=False)
    _import_total: int | None = field(default=None, init=False)
    _import_ack_window: int = field(default=EXPORT_ACK_WINDOW, init=False)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self.stats.running = True
        self.client.register_receive_callback(self._handle_message)
        self._thread = threading.Thread(
            target=self._run_loop,
            name="mesh-receiver",
            daemon=True,
        )
        self._thread.start()
        logger.info("Mesh receiver started")

    def stop(self) -> None:
        self._stop_event.set()
        self.client.unregister_receive_callback(self._handle_message)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self.stats.running = False
        logger.info("Mesh receiver stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                info = self.client.connection_info()
                if info.mock_mode:
                    for msg in self.client.poll_mock_inbox():
                        self.client.dispatch_message(msg)
            except Exception as exc:
                self.stats.last_error = str(exc)
                logger.exception("Receiver loop error")

            self._stop_event.wait(self.poll_interval)

    def _extend_import_grace(self) -> None:
        self.stats.import_grace_until = time.time() + IMPORT_GRACE_SECONDS

    def _import_grace_active(self) -> bool:
        return time.time() < self.stats.import_grace_until

    def _track_precinct(self, precinct_id: str) -> None:
        self.watched_precinct_ids.add(precinct_id.upper())

    def _send_export_ack(self, seq: int, total: int) -> None:
        from mesh_data_codec import encode_export_ack

        ok, detail = self.client.send_text(encode_export_ack(seq, total))
        if not ok:
            logger.warning("Failed to send export ACK %s/%s: %s", seq, total, detail)

    def _maybe_ack_imported_payload(self) -> None:
        if (
            self.stats.import_mode
            and self._import_seq is not None
            and self._import_total is not None
        ):
            window = max(1, self._import_ack_window)
            if (
                self._import_seq % window == 0
                or self._import_seq == self._import_total
            ):
                self._send_export_ack(self._import_seq, self._import_total)
                self._import_seq = None

    def _track_import_sequence(self, packet: MeshDataPacket) -> None:
        if packet.seq is not None:
            self._import_seq = packet.seq
        if packet.total is not None:
            self._import_total = packet.total

    def _unwrap_numbered_message(self, text: str) -> tuple[str, int | None, int | None]:
        numbered = parse_numbered_export_packet(text)
        if numbered is None:
            return text, None, None
        seq, total, body = numbered
        if self.stats.import_mode:
            self._import_seq = seq
            self._import_total = total
        return body, seq, total

    def _should_apply_status(self, precinct_id: str) -> bool:
        if self.stats.import_mode or self._import_grace_active():
            return True
        return precinct_id in self.watched_precinct_ids or not self.watched_precinct_ids

    def _handle_message(self, text: str, from_id: str | None = None) -> None:
        del from_id
        self.stats.packets_received += 1
        self.stats.last_packet = text

        body, _, _ = self._unwrap_numbered_message(text)

        data_packet = decode_mesh_data(body)
        if data_packet is not None:
            self._apply_data_packet(data_packet, text)
            return

        parsed = decode_updates(body)
        if not parsed:
            return

        applied: list[str] = []
        for update in parsed:
            precinct_id = update.precinct_id or self.legacy_precinct_id
            if precinct_id is None:
                logger.warning("Ignoring legacy packet without precinct context: %s", text)
                continue
            precinct_id = precinct_id.upper()
            if not self._should_apply_status(precinct_id):
                logger.debug("Ignoring packet for unwatched precinct %s", precinct_id)
                continue

            try:
                ensure_precinct_from_import(precinct_id)
                self._track_precinct(precinct_id)
                paths = paths_for_precinct(precinct_id)
                ensure_status_csv(paths.status)
                row = apply_remote_update(
                    update.house_id,
                    update.status_code,
                    path=paths.status,
                )
                applied.append(f"{precinct_id}/{row['house_id']} → {row['status_code']}")
                logger.info(
                    "Applied mesh update: %s/%s → %s",
                    precinct_id,
                    update.house_id,
                    update.status_code,
                )
            except Exception as exc:
                self.stats.last_error = str(exc)
                logger.exception(
                    "Failed to apply mesh update for %s/%s",
                    precinct_id,
                    update.house_id,
                )

        if applied:
            self._maybe_ack_imported_payload()
            self._record_activity(text, ", ".join(applied))

    def _apply_data_packet(self, packet: MeshDataPacket, raw_text: str) -> None:
        summary_parts: list[str] = []

        try:
            if packet.kind == MeshDataKind.ACK:
                return
            if packet.kind == MeshDataKind.START:
                self.stats.import_mode = True
                self._import_seq = None
                self._import_total = packet.total
                self._import_ack_window = max(
                    1,
                    packet.ack_window or EXPORT_ACK_WINDOW,
                )
                self._extend_import_grace()
                summary_parts.append("Full data import started")
            elif packet.kind == MeshDataKind.END:
                self.stats.import_mode = False
                self._import_seq = None
                self._import_total = None
                self._extend_import_grace()
                self.stats.import_complete_pending = True
                summary_parts.append("Full data import complete")
            elif packet.kind == MeshDataKind.INDEX:
                self._import_seq = packet.seq
                self._import_total = packet.total
                self._extend_import_grace()
            elif packet.kind == MeshDataKind.DISTRICT:
                assert packet.district_id is not None
                self._track_import_sequence(packet)
                district = upsert_district(
                    packet.district_id,
                    packet.district_name or packet.district_id,
                )
                self._extend_import_grace()
                summary_parts.append(f"District {district.id}")
                self.stats.data_imports_applied += 1
                self._maybe_ack_imported_payload()
            elif packet.kind == MeshDataKind.PRECINCT:
                assert packet.precinct_id is not None
                assert packet.district_id is not None
                self._track_import_sequence(packet)
                precinct = upsert_precinct(
                    packet.precinct_id,
                    packet.district_id,
                    packet.precinct_name or packet.precinct_id,
                )
                self._track_precinct(precinct.id)
                self._extend_import_grace()
                summary_parts.append(f"Precinct {precinct.id}")
                self.stats.data_imports_applied += 1
                self._maybe_ack_imported_payload()
            elif packet.kind == MeshDataKind.ADDRESSES:
                assert packet.precinct_id is not None
                self._track_import_sequence(packet)
                ensure_precinct_from_import(packet.precinct_id)
                self._track_precinct(packet.precinct_id)
                paths = paths_for_precinct(packet.precinct_id)
                ensure_status_csv(paths.status)
                rows = [
                    {"house_id": entry.house_id, "address": entry.address}
                    for entry in packet.addresses
                ]
                if rows:
                    result = apply_remote_addresses(rows, path=paths.addresses)
                    self._extend_import_grace()
                    summary_parts.append(
                        f"{packet.precinct_id} addresses "
                        f"(+{result['added']}, ~{result['updated']})"
                    )
                    self.stats.data_imports_applied += len(rows)
                self._maybe_ack_imported_payload()
        except Exception as exc:
            self.stats.last_error = str(exc)
            logger.exception("Failed to apply mesh data packet: %s", raw_text)
            return

        if summary_parts:
            self._record_activity(raw_text, ", ".join(summary_parts))

    def _record_activity(self, packet: str, summary: str) -> None:
        self.stats.updates_applied += 1
        self.stats.last_update = summary
        self.stats.last_error = ""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.stats.last_activity_at = now
        self.stats.recent_activity.appendleft(
            RecentActivity(
                at=now,
                packet=packet,
                summary=summary,
            )
        )
