"""
Meshtastic serial client with graceful fallback when no radio is connected.

When no device is found, the client runs in mock mode so the UI remains usable
for development and CSV workflow testing.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from config import (
    CONNECTION_BLUETOOTH,
    CONNECTION_SERIAL,
    MESHTASTIC_BLE_ADDRESS,
    MESHTASTIC_CHANNEL_NAME,
    MESHTASTIC_CONNECTION_TYPE,
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
    connection_type: str = CONNECTION_SERIAL


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
    """Thin wrapper around the Meshtastic serial or Bluetooth API."""

    dev_path: str | None = MESHTASTIC_PORT
    channel_name: str = MESHTASTIC_CHANNEL_NAME
    connection_type: str = MESHTASTIC_CONNECTION_TYPE
    ble_address: str | None = MESHTASTIC_BLE_ADDRESS
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
        """Attempt radio connection; fall back to mock mode on failure."""
        with self._lock:
            if self._interface is not None:
                return self.connection_info()
            if self._mock_mode:
                return self.connection_info()

            try:
                iface, display = self._open_interface()
            except Exception as exc:
                logger.warning("Meshtastic unavailable, using mock mode: %s", exc)
                self._reset_connection()
                return self._enter_mock_mode(_connect_failure_message(self.connection_type, exc))

            self._interface = iface
            self._connected_port = display
            self._mock_mode = False
            self._channel_index = _resolve_channel_index(iface, self.channel_name)

            _set_active_client(self)
            _ensure_global_pubsub()

            if self._channel_index is None:
                message = (
                    f"Connected to radio on {display}, but channel "
                    f"'{self.channel_name}' was not found. "
                    "Add it in the Meshtastic app, then click Reconnect."
                )
            else:
                message = (
                    f"Connected to radio on {display}, "
                    f"channel '{self.channel_name}' (index {self._channel_index})"
                )

            logger.info(message)
            return ConnectionInfo(
                connected=True,
                mock_mode=False,
                port=display,
                message=message,
                channel_name=self.channel_name,
                channel_index=self._channel_index,
                connection_type=self.connection_type,
            )

    def _open_interface(self) -> tuple[object, str]:
        """Open a Meshtastic interface. Returns (interface, display name)."""
        if self.connection_type == CONNECTION_BLUETOOTH:
            import meshtastic.ble_interface  # type: ignore[import-untyped]

            address = self.ble_address
            if address:
                _bluez_disconnect(address)
                time.sleep(2.0)
            iface = meshtastic.ble_interface.BLEInterface(address=address)
            client = getattr(iface, "client", None)
            display = (
                address
                or getattr(client, "address", None)
                or "bluetooth"
            )
            return iface, f"bluetooth:{display}"

        port = _discover_meshtastic_port(self.dev_path)
        if port is None:
            raise RuntimeError("No Meshtastic serial radio detected")

        import meshtastic.serial_interface  # type: ignore[import-untyped]

        iface = meshtastic.serial_interface.SerialInterface(
            devPath=port,
            connectNow=True,
            timeout=15,
        )
        stream = getattr(iface, "stream", None)
        if stream is None:
            raise RuntimeError(f"Could not open serial port {port}")
        return iface, port

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
            connection_type=self.connection_type,
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
                connection_type=self.connection_type,
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
            connection_type=self.connection_type,
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


def list_ble_devices() -> list[tuple[str, str]]:
    """Discover Meshtastic BLE radios via advertisement scan plus OS paired list.

    Advertisement scan typically takes about 10 seconds. Linux Mint often keeps a
    paired radio *connected*, which hides it from the scan — those still appear
    via bluetoothctl so the user can pick a MAC.
    """
    found: dict[str, str] = {}

    try:
        from meshtastic.ble_interface import BLEInterface  # type: ignore[import-untyped]

        for device in BLEInterface.scan():
            address = str(getattr(device, "address", "") or "").strip()
            name = str(getattr(device, "name", "") or "").strip() or address
            if address:
                found[address.upper()] = name
    except Exception:
        logger.exception("BLE advertisement scan failed")

    for address, name in _bluez_devices("Paired"):
        found.setdefault(address.upper(), name)

    logger.info("BLE discovery found %s device(s)", len(found))
    return [(address, name) for address, name in found.items()]


def list_ble_connected_addresses() -> set[str]:
    """MAC addresses currently held by the OS Bluetooth stack."""
    return {address.upper() for address, _ in _bluez_devices("Connected")}


def _bluez_devices(kind: str) -> list[tuple[str, str]]:
    """Parse `bluetoothctl devices Paired|Connected`. Empty on Windows / if missing."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices", kind],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return _parse_bluetoothctl_devices(result.stdout)


def _parse_bluetoothctl_devices(output: str) -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 2 or parts[0] != "Device":
            continue
        address = parts[1].strip()
        name = parts[2].strip() if len(parts) > 2 else address
        if address:
            devices.append((address, name))
    return devices


def _bluez_disconnect(address: str) -> None:
    """Drop an OS-held BLE connection so Meshtastic can take the GATT session."""
    address = address.strip()
    if not address:
        return
    try:
        result = subprocess.run(
            ["bluetoothctl", "disconnect", address],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        logger.info(
            "bluetoothctl disconnect %s: %s",
            address,
            (result.stdout or result.stderr or "").strip() or result.returncode,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("bluetoothctl disconnect skipped: %s", exc)


def _connect_failure_message(connection_type: str, exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    if connection_type == CONNECTION_BLUETOOTH:
        return (
            "Bluetooth connect failed — running in offline mock mode. "
            f"{detail} "
            "On Linux Mint: pair and trust the radio, then Disconnect it in "
            "Bluetooth settings (stay paired). The OS connection hides the "
            "radio from scan and blocks the app. Close the Meshtastic phone app too."
        )
    return (
        "No Meshtastic radio detected — running in offline mock mode. "
        "CSV and UI work normally; mesh send/receive is simulated."
    )
