"""
Meshtastic serial client with graceful fallback when no radio is connected.

When no device is found, the client runs in mock mode so the UI remains usable
for development and CSV workflow testing.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from config import MESHTASTIC_PORT

logger = logging.getLogger(__name__)

ReceiveCallback = Callable[[str], None]


@dataclass
class ConnectionInfo:
    connected: bool
    mock_mode: bool
    port: str | None
    message: str


def _discover_meshtastic_port(explicit: str | None = None) -> str | None:
    """Return a single Meshtastic serial port, or None if unavailable."""
    if explicit:
        return explicit

    try:
        import meshtastic.util

        ports = meshtastic.util.findPorts(True)
    except Exception:
        logger.exception("Port discovery failed")
        return None

    if len(ports) == 1:
        return ports[0]
    if len(ports) > 1:
        logger.warning(
            "Multiple Meshtastic ports detected (%s); set MESHTASTIC_PORT in config.py",
            ports,
        )
    return None


@dataclass
class MeshtasticClient:
    """Thin wrapper around the Meshtastic serial API."""

    dev_path: str | None = MESHTASTIC_PORT
    _interface: object | None = field(default=None, init=False, repr=False)
    _mock_mode: bool = field(default=False, init=False)
    _connected_port: str | None = field(default=None, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _receive_callbacks: list[ReceiveCallback] = field(default_factory=list, init=False)
    _mock_inbox: deque[str] = field(default_factory=deque, init=False)
    _pubsub_registered: bool = field(default=False, init=False)

    def connect(self) -> ConnectionInfo:
        """Attempt serial connection; fall back to mock mode on failure."""
        with self._lock:
            if self._interface is not None:
                return self.connection_info()
            if self._mock_mode:
                return self.connection_info()

            port = _discover_meshtastic_port(self.dev_path)
            if port is None:
                return self._enter_mock_mode(
                    "No Meshtastic radio detected — running in offline mock mode. "
                    "CSV and UI work normally; mesh send/receive is simulated."
                )

            try:
                import meshtastic.serial_interface  # type: ignore[import-untyped]
                from pubsub import pub  # type: ignore[import-untyped]

                iface = meshtastic.serial_interface.SerialInterface(
                    devPath=port,
                    connectNow=True,
                    timeout=15,
                )

                # SerialInterface can return a half-initialized object when probing fails.
                stream = getattr(iface, "stream", None)
                if stream is None:
                    raise RuntimeError(f"Could not open serial port {port}")

                self._interface = iface
                self._connected_port = port
                self._mock_mode = False

                if not self._pubsub_registered:
                    pub.subscribe(self._on_receive, "meshtastic.receive.text")
                    self._pubsub_registered = True

                logger.info("Connected to Meshtastic on %s", port)
                return ConnectionInfo(
                    connected=True,
                    mock_mode=False,
                    port=port,
                    message=f"Connected to radio on {port}",
                )

            except Exception as exc:
                logger.warning("Meshtastic unavailable, using mock mode: %s", exc)
                self._reset_connection()
                return self._enter_mock_mode(
                    "No Meshtastic radio detected — running in offline mock mode. "
                    "CSV and UI work normally; mesh send/receive is simulated."
                )

    def _enter_mock_mode(self, message: str) -> ConnectionInfo:
        self._interface = None
        self._mock_mode = True
        self._connected_port = None
        return ConnectionInfo(
            connected=False,
            mock_mode=True,
            port=None,
            message=message,
        )

    def connection_info(self) -> ConnectionInfo:
        if self._mock_mode:
            return ConnectionInfo(
                connected=False,
                mock_mode=True,
                port=None,
                message="Mock mode — no radio connected",
            )
        return ConnectionInfo(
            connected=self._interface is not None,
            mock_mode=False,
            port=self._connected_port,
            message=f"Connected to radio on {self._connected_port}",
        )

    def send_text(self, text: str) -> tuple[bool, str]:
        """Send a text packet over the mesh (or log in mock mode)."""
        with self._lock:
            info = self.connect()
            if info.mock_mode:
                logger.info("[MOCK TX] %s", text)
                return True, f"Mock transmit: {text}"

            try:
                assert self._interface is not None
                self._interface.sendText(text, wantAck=False)
                logger.info("[TX] %s", text)
                return True, f"Sent: {text}"
            except Exception as exc:
                logger.error("Send failed: %s", exc)
                self._reset_connection()
                return False, f"Send failed: {exc}"

    def send_many(self, messages: list[str]) -> tuple[int, list[str]]:
        """Send multiple packets; returns (success_count, error_messages)."""
        errors: list[str] = []
        success = 0
        for msg in messages:
            ok, detail = self.send_text(msg)
            if ok:
                success += 1
            else:
                errors.append(detail)
        return success, errors

    def register_receive_callback(self, callback: ReceiveCallback) -> None:
        if callback not in self._receive_callbacks:
            self._receive_callbacks.append(callback)

    def unregister_receive_callback(self, callback: ReceiveCallback) -> None:
        if callback in self._receive_callbacks:
            self._receive_callbacks.remove(callback)

    def mock_inject(self, text: str) -> None:
        """Simulate an incoming mesh message (for testing without hardware)."""
        self._mock_inbox.append(text)
        for cb in list(self._receive_callbacks):
            try:
                cb(text)
            except Exception:
                logger.exception("Receive callback error")

    def poll_mock_inbox(self) -> list[str]:
        """Drain simulated incoming messages (used by receiver thread in mock mode)."""
        messages: list[str] = []
        while self._mock_inbox:
            messages.append(self._mock_inbox.popleft())
        return messages

    def _on_receive(self, packet: dict, interface: object | None = None) -> None:
        text = self._extract_text(packet)
        if not text:
            return
        for cb in list(self._receive_callbacks):
            try:
                cb(text)
            except Exception:
                logger.exception("Receive callback error")

    @staticmethod
    def _extract_text(packet: dict) -> str | None:
        try:
            decoded = packet.get("decoded") or {}
            data = decoded.get("data") or {}
            text = data.get("text")
            if text:
                return str(text).strip()
            payload = data.get("payload")
            if payload:
                if isinstance(payload, (bytes, bytearray)):
                    return payload.decode("utf-8", errors="replace").strip()
                return str(payload).strip()
        except Exception:
            logger.exception("Failed to parse incoming packet")
        return None

    def _reset_connection(self) -> None:
        try:
            if self._interface is not None and hasattr(self._interface, "close"):
                self._interface.close()
        except Exception:
            pass
        self._interface = None
        self._connected_port = None

    def close(self) -> None:
        with self._lock:
            self._reset_connection()
            self._mock_mode = False


def list_serial_ports() -> list[str]:
    """Return Meshtastic-capable serial ports (empty if none found)."""
    try:
        import meshtastic.util

        return meshtastic.util.findPorts(True)
    except Exception:
        return []
