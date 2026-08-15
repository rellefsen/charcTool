"""Periodic non-green heartbeat transmission for district transmitters."""

from __future__ import annotations

import logging
import random
import threading
import time

from config import HEARTBEAT_INTERVAL_SECONDS, STATUS_GREEN, SYNC_PACKET_DELAY
from csv_store import read_all
from meshtastic_client import MeshtasticClient
from packet_codec import build_heartbeat_packets
from precinct_store import list_precincts, paths_for_precinct
from recent_clears_store import get_recent_clears, prune_recent_clears
from sync_state import compute_non_green_rows

logger = logging.getLogger(__name__)


def send_precinct_heartbeat(
    client: MeshtasticClient,
    precinct_id: str,
    *,
    delay_seconds: float = SYNC_PACKET_DELAY,
) -> tuple[int, list[str]]:
    """Send one heartbeat sequence for a precinct. Returns packets sent and errors."""
    paths = paths_for_precinct(precinct_id)
    rows = read_all(paths.status)
    non_green = compute_non_green_rows(rows)
    recent_ids = get_recent_clears(precinct_id)
    recent_rows = [(house_id, STATUS_GREEN) for house_id in recent_ids]

    packets = build_heartbeat_packets(precinct_id, non_green, recent_rows)
    success, errors = client.send_many(packets, delay_seconds=delay_seconds)
    if success == len(packets):
        prune_recent_clears(precinct_id, recent_ids)
    return success, errors


def send_district_heartbeats(
    client: MeshtasticClient,
    district_id: str,
    *,
    delay_seconds: float = SYNC_PACKET_DELAY,
) -> list[tuple[str, int, list[str]]]:
    """Send heartbeats for every precinct in a district."""
    results: list[tuple[str, int, list[str]]] = []
    for precinct in list_precincts(district_id):
        success, errors = send_precinct_heartbeat(
            client,
            precinct.id,
            delay_seconds=delay_seconds,
        )
        results.append((precinct.id, success, errors))
    return results


class HeartbeatService:
    """Background thread that sends district heartbeats on a fixed interval."""

    def __init__(
        self,
        client: MeshtasticClient,
        district_id: str,
        *,
        precinct_id: str | None = None,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        packet_delay_seconds: float = SYNC_PACKET_DELAY,
    ) -> None:
        self.client = client
        self.district_id = district_id.strip().upper()
        self.precinct_id = precinct_id.strip().upper() if precinct_id else None
        self.interval_seconds = interval_seconds
        self.packet_delay_seconds = packet_delay_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.last_run_at = 0.0
        self.last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="mesh-heartbeat",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Heartbeat service started for %s",
            self.precinct_id or f"district {self.district_id}",
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("Heartbeat service stopped")

    def _run_loop(self) -> None:
        # Stagger the first run so multiple districts do not align on the hour.
        time.sleep(random.uniform(0.0, min(60.0, self.interval_seconds * 0.05)))
        while not self._stop_event.is_set():
            try:
                if self.precinct_id:
                    send_precinct_heartbeat(
                        self.client,
                        self.precinct_id,
                        delay_seconds=self.packet_delay_seconds,
                    )
                else:
                    send_district_heartbeats(
                        self.client,
                        self.district_id,
                        delay_seconds=self.packet_delay_seconds,
                    )
                self.last_run_at = time.time()
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Heartbeat service error")

            self._stop_event.wait(self.interval_seconds)
