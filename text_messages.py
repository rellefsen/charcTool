"""Thread-safe text message log synced into Streamlit session state."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from config import MESH_MAX_PAYLOAD_BYTES

_lock = threading.Lock()
_pending: deque[TextMessage] = deque()


class TextMessageError(ValueError):
    """Raised when a text message fails validation."""


@dataclass(frozen=True)
class TextMessage:
    at: str
    direction: str
    text: str
    from_id: str | None = None


def format_message_text(sender: str | None, text: str) -> str:
    if sender:
        return f"{sender}: {text}"
    return text


def validate_message(text: str, *, max_bytes: int = MESH_MAX_PAYLOAD_BYTES) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise TextMessageError("Message is required.")
    if len(cleaned.encode("utf-8")) > max_bytes:
        raise TextMessageError(
            f"Message exceeds the {max_bytes}-byte mesh limit "
            f"({len(cleaned.encode('utf-8'))} bytes)."
        )
    return cleaned


def _enqueue(message: TextMessage) -> TextMessage:
    with _lock:
        _pending.appendleft(message)
    return message


def record_sent(text: str, from_id: str | None = None) -> TextMessage:
    cleaned = validate_message(text)
    return _enqueue(
        TextMessage(
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            direction="sent",
            text=cleaned,
            from_id=from_id,
        )
    )


def record_received(text: str, from_id: str | None = None) -> TextMessage | None:
    from mesh_data_codec import is_data_packet
    from packet_codec import is_status_packet

    cleaned = text.strip()
    if not cleaned or is_status_packet(cleaned) or is_data_packet(cleaned):
        return None

    return _enqueue(
        TextMessage(
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            direction="received",
            text=cleaned,
            from_id=from_id,
        )
    )


def drain_pending() -> list[TextMessage]:
    with _lock:
        pending = list(_pending)
        _pending.clear()
    return pending


def clear_messages() -> None:
    with _lock:
        _pending.clear()
