"""Application configuration and status code definitions."""

from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
CSV_PATH = DATA_DIR / "neighborhood_status.csv"

# CSV schema
CSV_FIELDS = ("house_id", "status_code", "timestamp")

# Status codes — single-letter wire format in mesh packets
STATUS_RED = "RED"
STATUS_YELLOW = "YELLOW"
STATUS_GREEN = "GREEN"

STATUS_CODES = (STATUS_RED, STATUS_YELLOW, STATUS_GREEN)

STATUS_WIRE = {
    STATUS_RED: "R",
    STATUS_YELLOW: "Y",
    STATUS_GREEN: "G",
}

STATUS_FROM_WIRE = {v: k for k, v in STATUS_WIRE.items()}

# UI labels and colors (high contrast for accessibility)
STATUS_LABELS = {
    STATUS_RED: "RED — Life threatening, immediate assistance",
    STATUS_YELLOW: "YELLOW — Assistance needed",
    STATUS_GREEN: "GREEN — OK",
}

STATUS_COLORS = {
    STATUS_RED: "#DC2626",
    STATUS_YELLOW: "#EAB308",
    STATUS_GREEN: "#16A34A",
}

STATUS_BG = {
    STATUS_RED: "#FEE2E2",
    STATUS_YELLOW: "#FEF9C3",
    STATUS_GREEN: "#DCFCE7",
}

# Mesh packet prefix — identifies neighborhood-status messages
PACKET_PREFIX = "NS"

# Default sample houses for first-time setup
DEFAULT_HOUSES = [f"H{i:03d}" for i in range(1, 11)]

# Meshtastic serial (None = auto-detect)
MESHTASTIC_PORT: str | None = None

# Meshtastic mesh channel for neighborhood status traffic
MESHTASTIC_CHANNEL_NAME = "charcStatus"

# Receiver poll interval (seconds)
RECEIVER_POLL_INTERVAL = 2.0

# Max UTF-8 bytes for a Meshtastic text payload (mesh_pb2.Constants.DATA_PAYLOAD_LEN)
MESH_MAX_PAYLOAD_BYTES = 233

# Delay between chunked sync packets so LoRa can finish each transmission
SYNC_PACKET_DELAY = 2.0
