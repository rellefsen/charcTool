"""
Meshtastic serial client with graceful fallback when no radio is connected.

When no device is found, the client runs in mock mode so the UI remains usable
for development and CSV workflow testing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from config import (
    EXPORT_ACK_TIMEOUT,
    EXPORT_MAX_RETRIES,
    EXPORT_PACKET_DELAY,
    MESHTASTIC_CHANNEL_NAME,
    MESHTASTIC_PORT,
    SYNC_PACKET_DELAY,
)

logger = logging.getLogger(__name__)

ReceiveCallback = Callable[[str, str | None], None]
ProgressCallback = Callable[[int, int], None]
WaitingCallback = Callable[[int, int, float], None]

_active_client: "MeshtasticClient | None" = None
_global_pubsub_registered = False


def _set_active_client(client: "MeshtasticClient | None") -> None:
    global _active_client
    _active_client = client


def _global_pubsub_handler(packet: dict, interface: object | None = None) -> None:
    client = _active_client
    if client is None:
        return
    client._process_incoming_packet(packet)


def _ensure_global_pubsub() -> None:
    global _global_pubsub_registered
    if _global_pubsub_registered:
        return
    from pubsub import pub  # type: ignore[import-untyped]

    pub.subscribe(_global_pubsub_handler, "meshtastic.receive.text")
    _global_pubsub_registered = True


@dataclass
class ConnectionInfo:
    connected: bool
    mock_mode: bool
    port: str | None
    message: str
    channel_name: str | None = None
    channel_index: int | None = None


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


def _resolve_channel_index(iface: object, channel_name: str) -> int | None:
    """Look up a named channel index on the connected radio."""
    try:
        node = iface.getNode("^local")
        channel = node.getChannelByName(channel_name)
        if channel is None:
            logger.warning("Channel %r not found on radio", channel_name)
            return None
        return int(channel.index)
    except Exception:
        logger.exception("Failed to resolve channel %r", channel_name)
        return None


@dataclass
class MeshtasticClient:
    """Thin wrapper around the Meshtastic serial API."""

    dev_path: str | None = MESHTASTIC_PORT
    channel_name: str = MESHTASTIC_CHANNEL_NAME
    _interface: object | None = field(default=None, init=False, repr=False)
    _mock_mode: bool = field(default=False, init=False)
    _connected_port: str | None = field(default=None, init=False)
    _channel_index: int | None = field(default=None, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _receive_callbacks: list[ReceiveCallback] = field(default_factory=list, init=False)
    _mock_inbox: deque[str] = field(default_factory=deque, init=False)

    def local_node_id(self) -> str | None:
        """Return this radio's mesh node ID, if known."""
        with self._lock:
            if self._mock_mode:
                return "LOCAL"
            if self._interface is None:
                return None
            try:
                user = self._interface.getMyUser()
                if user and user.get("id"):
                    return str(user["id"])
                if self._interface.myInfo is not None:
                    return self._interface._nodeNumToId(
                        self._interface.myInfo.my_node_num,
                        False,
                    )
            except Exception:
                logger.exception("Failed to read local node id")
            return None

    def local_node_name(self) -> str | None:
        """Return this radio's friendly name, if known."""
        with self._lock:
            if self._mock_mode:
                return "Local Radio"
            if self._interface is None:
                return None
            try:
                name = self._interface.getLongName()
                if name:
                    return str(name)
                name = self._interface.getShortName()
                if name:
                    return str(name)
            except Exception:
                logger.exception("Failed to read local node name")
            return self.local_node_id()

    @staticmethod
    def _user_display_name(user: dict | None) -> str | None:
        if not user:
            return None
        return str(user.get("longName") or user.get("shortName") or user.get("id") or "").strip() or None

    def node_display_name(self, node_id: str | None) -> str | None:
        """Resolve a mesh node ID to a friendly radio name."""
        if not node_id:
            return None
        if node_id == "LOCAL":
            return "Local Radio"
        if node_id == "!MOCK":
            return "Mock Radio"

        with self._lock:
            if self._interface is None or self._mock_mode:
                return node_id

            try:
                iface = self._interface
                nodes = iface.nodesByNum or {}

                for node in nodes.values():
                    user = node.get("user") or {}
                    if user.get("id") == node_id:
                        name = self._user_display_name(user)
                        if name:
                            return name

                if node_id.startswith("!"):
                    num = int(node_id[-8:], 16)
                    node = nodes.get(num)
                    if node:
                        name = self._user_display_name(node.get("user"))
                        if name:
                            return name
            except Exception:
                logger.exception("Failed to resolve node name for %s", node_id)

            return node_id

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
                self._channel_index = _resolve_channel_index(iface, self.channel_name)

                _set_active_client(self)
                _ensure_global_pubsub()

                if self._channel_index is None:
                    message = (
                        f"Connected to radio on {port}, but channel "
                        f"'{self.channel_name}' was not found. "
                        "Add it in the Meshtastic app, then click Reconnect."
                    )
                else:
                    message = (
                        f"Connected to radio on {port}, "
                        f"channel '{self.channel_name}' (index {self._channel_index})"
                    )

                logger.info(message)
                return ConnectionInfo(
                    connected=True,
                    mock_mode=False,
                    port=port,
                    message=message,
                    channel_name=self.channel_name,
                    channel_index=self._channel_index,
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
        self._channel_index = None
        _set_active_client(self)
        return ConnectionInfo(
            connected=False,
            mock_mode=True,
            port=None,
            message=message,
            channel_name=self.channel_name,
            channel_index=None,
        )

    def connection_info(self) -> ConnectionInfo:
        if self._mock_mode:
            return ConnectionInfo(
                connected=False,
                mock_mode=True,
                port=None,
                message=f"Mock mode — no radio connected (channel: {self.channel_name})",
                channel_name=self.channel_name,
                channel_index=None,
            )
        if self._channel_index is None and self._interface is not None:
            message = (
                f"Connected to radio on {self._connected_port}, but channel "
                f"'{self.channel_name}' was not found"
            )
        else:
            message = (
                f"Connected to radio on {self._connected_port}, "
                f"channel '{self.channel_name}'"
                + (f" (index {self._channel_index})" if self._channel_index is not None else "")
            )
        return ConnectionInfo(
            connected=self._interface is not None,
            mock_mode=False,
            port=self._connected_port,
            message=message,
            channel_name=self.channel_name,
            channel_index=self._channel_index,
        )

    def send_text(self, text: str) -> tuple[bool, str]:
        """Send a text packet over the mesh (or log in mock mode)."""
        with self._lock:
            info = self.connect()
            if info.mock_mode:
                logger.info("[MOCK TX %s] %s", self.channel_name, text)
                from_id = self.local_node_id()
            elif self._channel_index is None:
                msg = f"Cannot send — channel '{self.channel_name}' not found on radio"
                logger.error(msg)
                return False, msg
            else:
                from_id = None
                try:
                    assert self._interface is not None
                    self._interface.sendText(
                        text,
                        wantAck=False,
                        channelIndex=self._channel_index,
                    )
                    logger.info("[TX ch=%s] %s", self.channel_name, text)
                    return True, f"Sent on {self.channel_name}: {text}"
                except Exception as exc:
                    logger.error("Send failed: %s", exc)
                    self._reset_connection()
                    return False, f"Send failed: {exc}"

        if info.mock_mode:
            # Dispatch outside the lock so receive handlers can send replies (e.g. export ACKs).
            self.dispatch_message(text, from_id)
            return True, f"Mock transmit on {self.channel_name}: {text}"

        return False, "Unexpected send state"

    def reconnect(self) -> ConnectionInfo:
        """Close any existing session and attempt a fresh radio connection."""
        with self._lock:
            self._reset_connection()
            self._mock_mode = False
        _set_active_client(self)
        return self.connect()

    def send_many(
        self,
        messages: list[str],
        delay_seconds: float = SYNC_PACKET_DELAY,
        on_progress: ProgressCallback | None = None,
        on_waiting: WaitingCallback | None = None,
    ) -> tuple[int, list[str]]:
        """Send multiple packets with a delay between each for LoRa airtime."""
        errors: list[str] = []
        success = 0
        total = len(messages)

        for index, msg in enumerate(messages):
            if on_progress:
                on_progress(index + 1, total)

            ok, detail = self.send_text(msg)
            if ok:
                success += 1
            else:
                errors.append(detail)

            if delay_seconds > 0 and index < total - 1:
                if on_waiting:
                    on_waiting(index + 1, total, delay_seconds)
                time.sleep(delay_seconds)

        return success, errors

    def wait_for_export_ack(
        self,
        seq: int,
        total: int,
        timeout_seconds: float = EXPORT_ACK_TIMEOUT,
    ) -> bool:
        """Block until the matching export ACK arrives or the timeout expires."""
        from mesh_data_codec import encode_export_ack

        expected = encode_export_ack(seq, total).upper()
        event = threading.Event()

        def _on_ack(text: str, from_id: str | None = None) -> None:
            del from_id
            if text.strip().upper() == expected:
                event.set()

        self.register_receive_callback(_on_ack)
        try:
            return event.wait(timeout_seconds)
        finally:
            self.unregister_receive_callback(_on_ack)

    def send_export_with_acks(
        self,
        payloads: list[str],
        *,
        delay_seconds: float = EXPORT_PACKET_DELAY,
        ack_timeout_seconds: float = EXPORT_ACK_TIMEOUT,
        max_retries: int = EXPORT_MAX_RETRIES,
        on_progress: ProgressCallback | None = None,
        on_waiting: WaitingCallback | None = None,
        on_ack_wait: Callable[[int, int, int], None] | None = None,
    ) -> tuple[int, list[str]]:
        """
        Send a full export with per-packet ACKs and retries.

        Returns the number of payload packets acknowledged and any error messages.
        """
        from mesh_data_codec import encode_export_end, encode_export_index, encode_export_start

        errors: list[str] = []
        acked = 0
        total = len(payloads)

        ok, detail = self.send_text(encode_export_start(total))
        if not ok:
            return 0, [detail]

        if delay_seconds > 0:
            time.sleep(delay_seconds)

        for seq, payload in enumerate(payloads, start=1):
            if on_progress:
                on_progress(seq, total)

            acknowledged = False
            for attempt in range(1, max_retries + 1):
                from mesh_data_codec import encode_export_ack

                expected_ack = encode_export_ack(seq, total).upper()
                ack_event = threading.Event()

                def _on_ack(text: str, from_id: str | None = None) -> None:
                    del from_id
                    if text.strip().upper() == expected_ack:
                        ack_event.set()

                self.register_receive_callback(_on_ack)
                try:
                    ok, detail = self.send_text(encode_export_index(seq, total))
                    if not ok:
                        errors.append(f"Packet {seq}/{total} index: {detail}")
                        break

                    if delay_seconds > 0:
                        time.sleep(min(delay_seconds, 1.0))

                    ok, detail = self.send_text(payload)
                    if not ok:
                        errors.append(f"Packet {seq}/{total} send: {detail}")
                        break

                    if on_ack_wait:
                        on_ack_wait(seq, total, attempt)

                    if ack_event.wait(ack_timeout_seconds):
                        acknowledged = True
                        acked += 1
                        break
                finally:
                    self.unregister_receive_callback(_on_ack)

                if attempt < max_retries:
                    logger.warning(
                        "No ACK for export packet %s/%s (attempt %s/%s)",
                        seq,
                        total,
                        attempt,
                        max_retries,
                    )
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                else:
                    errors.append(
                        f"No ACK for packet {seq}/{total} after {max_retries} attempts"
                    )

            if delay_seconds > 0 and seq < total:
                if on_waiting:
                    on_waiting(seq, total, delay_seconds)
                time.sleep(delay_seconds)

        ok, detail = self.send_text(encode_export_end())
        if not ok:
            errors.append(detail)

        return acked, errors

    def register_receive_callback(self, callback: ReceiveCallback) -> None:
        if callback not in self._receive_callbacks:
            self._receive_callbacks.append(callback)

    def unregister_receive_callback(self, callback: ReceiveCallback) -> None:
        if callback in self._receive_callbacks:
            self._receive_callbacks.remove(callback)

    def dispatch_message(self, text: str, from_id: str | None = None) -> None:
        """Deliver a mesh text payload to all receive callbacks."""
        for cb in list(self._receive_callbacks):
            try:
                cb(text, from_id)
            except Exception:
                logger.exception("Receive callback error")

    def mock_inject(self, text: str, from_id: str | None = "!MOCK") -> None:
        """Simulate an incoming mesh message (for testing without hardware)."""
        self.dispatch_message(text, from_id)

    def poll_mock_inbox(self) -> list[str]:
        """Drain simulated incoming messages (used by receiver thread in mock mode)."""
        messages: list[str] = []
        while self._mock_inbox:
            messages.append(self._mock_inbox.popleft())
        return messages

    def _process_incoming_packet(self, packet: dict) -> None:
        if self._channel_index is not None:
            rx_channel = packet.get("channel", 0)
            try:
                channel_match = int(rx_channel) == int(self._channel_index)
            except (TypeError, ValueError):
                channel_match = rx_channel == self._channel_index
            if not channel_match:
                logger.debug(
                    "Ignoring packet on channel %s (want %s / %r)",
                    rx_channel,
                    self._channel_index,
                    self.channel_name,
                )
                return

        text = self._extract_text(packet)
        if not text:
            return
        from_id = packet.get("fromId")
        if from_id is not None:
            from_id = str(from_id)
        self.dispatch_message(text, from_id)

    @staticmethod
    def _extract_text(packet: dict) -> str | None:
        try:
            decoded = packet.get("decoded") or {}
            text = decoded.get("text")
            if text:
                return str(text).strip()

            payload = decoded.get("payload")
            if payload:
                if isinstance(payload, (bytes, bytearray)):
                    decoded_text = payload.decode("utf-8", errors="replace").strip()
                    if decoded_text:
                        return decoded_text
                elif isinstance(payload, str):
                    payload = payload.strip()
                    if payload:
                        return payload

            data = decoded.get("data") or {}
            if isinstance(data, dict):
                text = data.get("text")
                if text:
                    return str(text).strip()
                nested_payload = data.get("payload")
                if nested_payload:
                    if isinstance(nested_payload, (bytes, bytearray)):
                        return nested_payload.decode("utf-8", errors="replace").strip()
                    return str(nested_payload).strip()
            elif data:
                if isinstance(data, (bytes, bytearray)):
                    return data.decode("utf-8", errors="replace").strip()
                return str(data).strip()

            if packet.get("text"):
                return str(packet["text"]).strip()
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
        self._channel_index = None

    def close(self) -> None:
        with self._lock:
            if _active_client is self:
                _set_active_client(None)
            self._reset_connection()
            self._mock_mode = False
            self._receive_callbacks.clear()


def list_serial_ports() -> list[str]:
    """Return Meshtastic-capable serial ports (empty if none found)."""
    try:
        import meshtastic.util

        return meshtastic.util.findPorts(True)
    except Exception:
        return []
