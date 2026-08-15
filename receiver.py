"""Background mesh listener that updates the local CSV from incoming packets."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pathlib import Path

from csv_store import apply_remote_update, ensure_status_csv, reconcile_non_green_snapshot
from config import RECEIVER_POLL_INTERVAL
from meshtastic_client import MeshtasticClient
from packet_codec import (
    ControlPacketKind,
    decode_updates,
    parse_control_packet,
)
from precinct_store import paths_for_precinct

logger = logging.getLogger(__name__)

RECENT_ACTIVITY_LIMIT = 15
_LAST_HEARTBEAT_FILE = "last_heartbeat_at.txt"


def _last_heartbeat_path(precinct_id: str) -> Path:
    return paths_for_precinct(precinct_id).status.parent / _LAST_HEARTBEAT_FILE


def _read_saved_heartbeat_at(precinct_id: str) -> str | None:
    path = _last_heartbeat_path(precinct_id)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _write_saved_heartbeat_at(precinct_id: str, at: str) -> None:
    path = _last_heartbeat_path(precinct_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(at, encoding="utf-8")


@dataclass
class RecentActivity:
    at: str
    packet: str
    summary: str


@dataclass
class HeartbeatSession:
    precinct_id: str
    snapshot_house_ids: set[str] = field(default_factory=set)
    snapshot_at: str = ""


@dataclass
class ReceiverStats:
    running: bool = False
    packets_received: int = 0
    updates_applied: int = 0
    last_packet: str = ""
    last_update: str = ""
    last_error: str = ""
    last_activity_at: str = ""
    last_heartbeat_at: dict[str, str] = field(default_factory=dict)
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
    _heartbeat: HeartbeatSession | None = field(default=None, init=False)

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

    def _track_precinct(self, precinct_id: str) -> None:
        self.watched_precinct_ids.add(precinct_id.upper())

    def _should_apply_status(self, precinct_id: str) -> bool:
        return precinct_id in self.watched_precinct_ids or not self.watched_precinct_ids

    def _is_own_packet(self, from_id: str | None) -> bool:
        if not from_id:
            return False
        local = self.client.local_node_id()
        return bool(local) and from_id.upper() == local.upper()

    def _handle_message(self, text: str, from_id: str | None = None) -> None:
        self.stats.packets_received += 1
        self.stats.last_packet = text

        control = parse_control_packet(text)
        if control is not None:
            if self._is_own_packet(from_id):
                logger.debug("Ignoring own heartbeat/control packet")
                return
            self._handle_control_packet(control, text)
            return

        parsed = decode_updates(text)
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
                self._track_precinct(precinct_id)
                paths = paths_for_precinct(precinct_id)
                ensure_status_csv(paths.status)
                row = apply_remote_update(
                    update.house_id,
                    update.status_code,
                    path=paths.status,
                )
                applied.append(f"{precinct_id}/{row['house_id']} → {row['status_code']}")
                if (
                    self._heartbeat is not None
                    and self._heartbeat.precinct_id == precinct_id
                ):
                    self._heartbeat.snapshot_house_ids.add(row["house_id"].upper())
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
            self._record_activity(text, ", ".join(applied))

    def _handle_control_packet(self, control, raw_text: str) -> None:
        precinct_id = control.precinct_id
        if not self._should_apply_status(precinct_id):
            logger.debug("Ignoring control packet for unwatched precinct %s", precinct_id)
            return

        if control.kind == ControlPacketKind.HEARTBEAT_START:
            snapshot_at = control.snapshot_at or datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            self._heartbeat = HeartbeatSession(
                precinct_id=precinct_id,
                snapshot_at=snapshot_at,
            )
            self._track_precinct(precinct_id)
            self._record_activity(raw_text, f"{precinct_id} heartbeat started")
            return

        if control.kind == ControlPacketKind.RECENT_CLEARS:
            applied: list[str] = []
            paths = paths_for_precinct(precinct_id)
            ensure_status_csv(paths.status)
            for update in control.updates:
                try:
                    row = apply_remote_update(
                        update.house_id,
                        update.status_code,
                        path=paths.status,
                    )
                    applied.append(f"{precinct_id}/{row['house_id']} → {row['status_code']}")
                    if (
                        self._heartbeat is not None
                        and self._heartbeat.precinct_id == precinct_id
                    ):
                        self._heartbeat.snapshot_house_ids.add(row["house_id"].upper())
                except Exception as exc:
                    self.stats.last_error = str(exc)
                    logger.exception(
                        "Failed to apply recent clear for %s/%s",
                        precinct_id,
                        update.house_id,
                    )
            if applied:
                self._record_activity(raw_text, ", ".join(applied))
            return

        if control.kind == ControlPacketKind.HEARTBEAT_END:
            if self._heartbeat is None or self._heartbeat.precinct_id != precinct_id:
                logger.warning(
                    "Heartbeat end for %s without active session — ignoring reconcile",
                    precinct_id,
                )
                return

            paths = paths_for_precinct(precinct_id)
            ensure_status_csv(paths.status)
            previous = self.stats.last_heartbeat_at.get(precinct_id) or _read_saved_heartbeat_at(
                precinct_id
            )
            try:
                cleared = reconcile_non_green_snapshot(
                    self._heartbeat.snapshot_house_ids,
                    path=paths.status,
                    snapshot_at=self._heartbeat.snapshot_at,
                    previous_heartbeat_at=previous,
                )
            except Exception as exc:
                self.stats.last_error = str(exc)
                logger.exception("Failed to reconcile heartbeat for %s", precinct_id)
                return
            finally:
                self._heartbeat = None

            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.stats.last_heartbeat_at[precinct_id] = now
            _write_saved_heartbeat_at(precinct_id, now)
            summary_parts = [f"{precinct_id} heartbeat complete"]
            if cleared:
                summary_parts.append(
                    "cleared "
                    + ", ".join(f"{precinct_id}/{house_id}" for house_id in cleared)
                )
            self._record_activity(raw_text, "; ".join(summary_parts))

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
