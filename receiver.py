"""Background mesh listener that updates the local CSV from incoming packets."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from config import RECEIVER_POLL_INTERVAL
from csv_store import apply_remote_update
from meshtastic_client import MeshtasticClient
from packet_codec import decode_updates

logger = logging.getLogger(__name__)


@dataclass
class ReceiverStats:
    running: bool = False
    packets_received: int = 0
    updates_applied: int = 0
    last_packet: str = ""
    last_update: str = ""
    last_error: str = ""


@dataclass
class MeshReceiver:
    """Runs a daemon thread to process incoming neighborhood status packets."""

    client: MeshtasticClient
    poll_interval: float = RECEIVER_POLL_INTERVAL
    _thread: threading.Thread | None = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    stats: ReceiverStats = field(default_factory=ReceiverStats)

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
        self.client.connect()
        while not self._stop_event.is_set():
            try:
                info = self.client.connection_info()
                if info.mock_mode:
                    for msg in self.client.poll_mock_inbox():
                        self._handle_message(msg)
            except Exception as exc:
                self.stats.last_error = str(exc)
                logger.exception("Receiver loop error")

            self._stop_event.wait(self.poll_interval)

    def _handle_message(self, text: str) -> None:
        self.stats.packets_received += 1
        self.stats.last_packet = text

        parsed = decode_updates(text)
        if not parsed:
            return

        applied: list[str] = []
        for house_id, status_code in parsed:
            try:
                row = apply_remote_update(house_id, status_code)
                applied.append(f"{row['house_id']} → {row['status_code']}")
                logger.info("Applied mesh update: %s → %s", house_id, status_code)
            except Exception as exc:
                self.stats.last_error = str(exc)
                logger.exception("Failed to apply mesh update for %s", house_id)

        if applied:
            self.stats.updates_applied += len(applied)
            self.stats.last_update = ", ".join(applied)
            self.stats.last_error = ""
