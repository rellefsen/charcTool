# Block Status

Neighborhood block-captain status board over **Meshtastic**. Operators mark houses RED / YELLOW / BLACK / GREEN; district laptops or phones transmit changes, and an EOC laptop listens and updates the same local CSVs.

The GitHub repo is still [`rellefsen/charcTool`](https://github.com/rellefsen/charcTool). The mesh channel stays **`charcStatus`**. Status letters are **R / Y / K / G** (`K` is BLACK; `B` is the bulk packet kind).

The laptop app talks to a Meshtastic radio over **USB serial** or **Bluetooth**. If no radio is present, it runs in **mock mode** so you can still edit boards and test the UI.

## What it does

- **Transmitter** — edit a precinct board, sync status changes to the mesh, send hourly heartbeats of all non-green houses.
- **Receiver** — listen for `NS:` packets and apply them to local CSVs; on heartbeat end, clear RED/YELLOW houses that were not in the snapshot (missed GREEN). BLACK is never auto-cleared.
- **Organization** — districts and precincts, each with `house_addresses.csv` and `neighborhood_status.csv`.
- **Manual seeding** — copy org and precinct files to every laptop. There is no over-air full data export.
- **Text messages** — optional free-form chat on the same mesh channel.

Operational house data under `data/` is gitignored. It does not go to GitHub. Seed each node from a USB stick, a shared zip, or the sample [`charcTool-housing-data-sample.zip`](charcTool-housing-data-sample.zip) in this repo.

A simplified **Android sender** lives in [`android-sender/`](android-sender/README.md). District phones import a seed zip, pick a Meshtastic radio over BLE, choose the `charcStatus` channel by name, and **Send** / **Heartbeat** the same `NS:` packets as the laptop. It does not receive or edit org files. Sideload the **mesh** APK from [Releases](https://github.com/rellefsen/charcTool/releases/latest); **mock** is UI-only. Do not transmit the same precinct from phone and laptop at once.

## Prerequisites

### Both platforms

- **Python 3.10+** (3.11 or 3.12 recommended), with `pip` and the `venv` module
- Git (to clone the repo)
- A modern browser (the UI is Streamlit, usually at http://localhost:8501)
- Optional: a Meshtastic radio (Heltec, RAK, T-Beam, etc.) on current firmware
- Mesh channel name **`charcStatus`** on every radio, **same PSK**, same modem preset

Without a radio the app still starts. Mesh send/receive is simulated until you connect one.

### Linux

- `python3`, `python3-venv`, `python3-pip`
- USB serial: add your user to the **`dialout`** group (Debian/Ubuntu/Mint) or **`uucp`** (some distros), then log out and back in
- Bluetooth: **BlueZ** (`bluez`), user in the **`bluetooth`** group, adapter powered on
- Pair and **trust** the radio, then **disconnect** it in the OS. Leave it paired. If Mint/GNOME shows the device as Connected, the OS is holding the only BLE slot and the app cannot scan or connect.
- Close the Meshtastic phone app while the laptop is using BLE (one client at a time)

```bash
sudo usermod -aG dialout,bluetooth "$USER"
# log out and back in
```

### Windows

- Python from [python.org](https://www.python.org/downloads/) — check **Add python.exe to PATH**
- USB serial driver for the radio (often **CP210x**, **CH340**, or **STM32 VCP**)
- Bluetooth: pair the radio in Windows Settings first, then connect from the app. Close the Meshtastic phone app.
- PowerShell execution policy may block `setup.ps1` (see Windows install below)

## Install

Clone the repo, then run the setup script for your OS. It creates `.venv`, installs dependencies, and initializes default CSVs.

### Linux

```bash
git clone https://github.com/rellefsen/charcTool.git
cd charcTool
chmod +x setup.sh
./setup.sh
```

Start the app (each time, in a new terminal):

```bash
cd charcTool
source .venv/bin/activate
streamlit run app.py
```

### Windows

In **PowerShell**, from the project folder:

```powershell
git clone https://github.com/rellefsen/charcTool.git
cd charcTool
```

If scripts are blocked, run these as **two** separate commands:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\setup.ps1
```

Or run setup once without changing policy:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Start the app:

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

### Manual install (either OS)

```bash
python3 -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
# .\.venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Dependencies: Streamlit, Meshtastic Python API, Bleak (Bluetooth), pyserial, pandas, pypubsub.

## First-time radio setup

1. Flash current **Meshtastic** firmware on every radio.
2. Give unique long names (example: `EOC-OPS`, `SOUTH-TX`).
3. Create a **secondary** channel named `charcStatus` with the **same PSK** on every node. Block Status only uses that named channel.
4. Match modem preset (LongFast is the usual default). Raise hop limit if districts are several hops from the EOC (start at 5).
5. Laptop radios: **CLIENT**. Roof/coverage nodes: **ROUTER** or **ROUTER_LATE**.

### USB

1. Plug in the radio.
2. In the app: sidebar → **Radio settings** → **Serial USB**.
3. Pick the port or leave **Auto-detect** (if two radios are plugged in, pick one).
4. **Apply & reconnect**. Status should show connected, not mock mode, and a channel index for `charcStatus`.

Linux: if the port exists but open fails, you are probably missing the `dialout` group.

### Bluetooth

1. Pair and trust in the OS. Enter the PIN from the device screen (`bluetoothctl` on Linux).
2. **Disconnect** in the OS so the app can take the GATT session.
3. In the app: **Radio connection** → **Bluetooth** → **Scan** (~10 seconds), or paste the MAC.
4. **Apply & reconnect**.

CLI checks:

```bash
meshtastic --info              # USB
meshtastic --ble-scan
meshtastic --ble --info        # Bluetooth
```

## Seed house data on every laptop

Every node needs the same organization and addresses. Status files start **all GREEN**.

Copy these three kinds of files:

| File | Path |
|------|------|
| Organization | `data/organization.json` |
| Addresses | `data/precincts/{PRECINCT_ID}/house_addresses.csv` |
| Status | `data/precincts/{PRECINCT_ID}/neighborhood_status.csv` |

Do **not** put live operational CSVs in Git; `data/organization.json` and `data/precincts/` are gitignored. Seed each node from a USB stick or zip.

The repo includes a starter zip, [`charcTool-housing-data-sample.zip`](charcTool-housing-data-sample.zip) (`organization.json` plus CHARC01/02 and SOUTH01/02/03). Unzip it into `data/` on each laptop, or **Import seed** in the Android app.

`data/app_settings.json` is per laptop (channel, serial vs Bluetooth, heartbeat interval). It is also gitignored.

### `organization.json`

District IDs are 2–8 letters or numbers. Precinct IDs are the district ID plus a 2–4 character suffix (total 4–12 characters), all uppercase.

```json
{
  "districts": [
    { "id": "CHARC", "name": "North District" },
    { "id": "SOUTH", "name": "South District" }
  ],
  "precincts": [
    { "id": "CHARC01", "district_id": "CHARC", "name": "North Precinct 01" },
    { "id": "CHARC02", "district_id": "CHARC", "name": "North Precinct 02" },
    { "id": "SOUTH01", "district_id": "SOUTH", "name": "South Precinct 01" }
  ]
}
```

### `house_addresses.csv`

One file per precinct. Header required. `house_id` must match the status file (typically `H001` style, up to 8 characters).

```csv
house_id,address
H001,1 Oak St
H002,2 Oak St
H003,142 Pine Ave
```

### `neighborhood_status.csv`

One file per precinct. Header required. `status_code` is `RED`, `YELLOW`, `BLACK`, or `GREEN`. On the wire, BLACK is **`K`**. `timestamp` is UTC ISO-8601 with a `Z`. Seed every house **GREEN**.

```csv
house_id,status_code,timestamp
H001,GREEN,2026-08-14T05:21:42Z
H002,GREEN,2026-08-14T05:21:42Z
H003,GREEN,2026-08-14T05:21:42Z
```

## Day-of operations

| Site | Radio | Laptop mode |
|------|--------|-------------|
| District | CLIENT (USB or BLE) | **Transmitter** — laptop Streamlit, or the Android sender APK |
| Coverage / roofs | ROUTER | No laptop |
| EOC | CLIENT | **Receiver** — laptop only; apply incoming status |

Sidebar → **Radio** → **Open field checklist** for the printable standup guide (channel, delays, smoke test, failure symptoms).

Smoke test one district → EOC:

1. Both connected (not mock), channel index shown.
2. Send a short free-form text; EOC sees it.
3. Mark one house YELLOW, **SYNC TO MESH**; EOC updates.
4. Clear to GREEN (or **Send heartbeat now** if the clear was missed).

Defaults: 233-byte text payloads, ~2 s pause between packets (raise to 4–6 s if packets drop), heartbeat every 60 minutes.

## Tests

```bash
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
python3 test_smoke.py       # Windows: python test_smoke.py
```

The `pubsub` test is skipped if `pypubsub` is not installed; setup scripts install it via `requirements.txt`.
