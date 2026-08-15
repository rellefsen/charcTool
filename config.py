"""Application configuration and status code definitions."""

from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
PRECINCTS_DIR = DATA_DIR / "precincts"
ORGANIZATION_PATH = DATA_DIR / "organization.json"

# Legacy flat paths (migrated into data/precincts/{precinct_id}/ on startup)
CSV_PATH = DATA_DIR / "neighborhood_status.csv"
LAST_SYNC_PATH = DATA_DIR / "last_mesh_sync.csv"
ADDRESSES_PATH = DATA_DIR / "house_addresses.csv"
SETTINGS_PATH = DATA_DIR / "app_settings.json"

# Default district / first precinct for new installs and migration
DEFAULT_DISTRICT_ID = "CHARC"
DEFAULT_DISTRICT_NAME = "Default District"
DEFAULT_PRECINCT_ID = "CHARC01"
DEFAULT_PRECINCT_NAME = "Precinct 01"
DEFAULT_PRECINCT_SUFFIX = "01"

# CSV schema
CSV_FIELDS = ("house_id", "status_code", "timestamp")
ADDRESS_FIELDS = ("house_id", "address")

# Status codes — single-letter wire format in mesh packets
STATUS_RED = "RED"
STATUS_YELLOW = "YELLOW"
STATUS_BLACK = "BLACK"
STATUS_GREEN = "GREEN"

STATUS_CODES = (STATUS_RED, STATUS_YELLOW, STATUS_BLACK, STATUS_GREEN)

STATUS_WIRE = {
    STATUS_RED: "R",
    STATUS_YELLOW: "Y",
    STATUS_BLACK: "K",
    STATUS_GREEN: "G",
}

STATUS_FROM_WIRE = {v: k for k, v in STATUS_WIRE.items()}

# UI labels and colors (high contrast for accessibility)
STATUS_LABELS = {
    STATUS_RED: "RED — Life threatening, immediate assistance",
    STATUS_YELLOW: "YELLOW — Assistance needed",
    STATUS_BLACK: "BLACK — Death; help no longer needed",
    STATUS_GREEN: "GREEN — OK",
}

STATUS_COLORS = {
    STATUS_RED: "#DC2626",
    STATUS_YELLOW: "#EAB308",
    STATUS_BLACK: "#F9FAFB",
    STATUS_GREEN: "#16A34A",
}

STATUS_BG = {
    STATUS_RED: "#FEE2E2",
    STATUS_YELLOW: "#FEF9C3",
    STATUS_BLACK: "#111827",
    STATUS_GREEN: "#DCFCE7",
}

# Lower number = higher urgency (shown first in the UI)
STATUS_URGENCY = {
    STATUS_RED: 0,
    STATUS_YELLOW: 1,
    STATUS_BLACK: 2,
    STATUS_GREEN: 3,
}

STATUS_SORT_CAPTION = "Sorted by urgency: RED, YELLOW, BLACK, then GREEN."

# Mesh packet prefix — identifies neighborhood-status messages
PACKET_PREFIX = "NS"

# Default sample houses for first-time setup (60 houses exercises multi-packet bulk sync)
DEFAULT_HOUSES = [f"H{i:03d}" for i in range(1, 61)]

# Placeholder street name for auto-generated local addresses (UI only)
DEFAULT_STREET_NAME = "Oak St"

# Meshtastic serial (None = auto-detect)
MESHTASTIC_PORT: str | None = None

CONNECTION_SERIAL = "serial"
CONNECTION_BLUETOOTH = "bluetooth"
CONNECTION_TYPES = (CONNECTION_SERIAL, CONNECTION_BLUETOOTH)

# Default radio transport. Bluetooth needs a paired Meshtastic BLE device.
MESHTASTIC_CONNECTION_TYPE = CONNECTION_SERIAL
MESHTASTIC_BLE_ADDRESS: str | None = None

# Meshtastic mesh channel for neighborhood status traffic
MESHTASTIC_CHANNEL_NAME = "charcStatus"

# Receiver poll interval (seconds)
RECEIVER_POLL_INTERVAL = 2.0

# Max UTF-8 bytes for a Meshtastic text payload (mesh_pb2.Constants.DATA_PAYLOAD_LEN)
MESH_MAX_PAYLOAD_BYTES = 233

# Delay between chunked sync packets so LoRa can finish each transmission
SYNC_PACKET_DELAY = 2.0

# Hourly heartbeat of all non-green houses (seconds)
HEARTBEAT_INTERVAL_SECONDS = 3600.0

# Include explicit GREEN clears for this long after a house is cleared (seconds)
RECENT_CLEARS_WINDOW_SECONDS = 10800.0
