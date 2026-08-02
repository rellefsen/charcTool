#!/usr/bin/env bash
# Block Captain Meshtastic App — first-time setup
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Block Captain Meshtastic Setup ==="

# Python version check
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required but not found."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python version: $PY_VERSION"

# Virtual environment
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment in .venv ..."
    python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Installing dependencies ..."
pip install --upgrade pip
pip install -r requirements.txt

# Initialize data directory and CSV
echo "Initializing neighborhood status CSV ..."
python3 -c "from csv_store import init_csv, ensure_default_houses; init_csv(); added = ensure_default_houses(); print('  ->', init_csv()); print(f'  -> added {added} default houses')"
echo "Initializing local house addresses (UI only) ..."
python3 -c "from address_store import init_addresses, ensure_default_addresses; init_addresses(); added = ensure_default_addresses(); print('  ->', init_addresses()); print(f'  -> added {added} default addresses')"

# Serial port probe (informational only — app runs without a radio)
echo ""
echo "Serial port scan:"
python3 - <<'PY'
try:
    from meshtastic_client import list_serial_ports
    ports = list_serial_ports()
    if ports:
        for p in ports:
            print(f"  Found: {p}")
    else:
        print("  No Meshtastic/serial ports detected (mock mode will be used).")
except Exception as exc:
    print(f"  Port scan skipped: {exc}")
PY

# Connection fallback smoke test
echo ""
echo "Testing Meshtastic connection fallback ..."
python3 - <<'PY'
from meshtastic_client import MeshtasticClient

client = MeshtasticClient()
info = client.connect()
print(f"  Connected: {info.connected}")
print(f"  Mock mode: {info.mock_mode}")
print(f"  Message:   {info.message}")

ok, msg = client.send_text("NS:H001:G")
print(f"  Test send: ok={ok}, {msg}")
client.close()
print("  Fallback test passed.")
PY

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start the app:"
echo "  source .venv/bin/activate"
echo "  streamlit run app.py"
echo ""
echo "Open the URL shown (usually http://localhost:8501)"
