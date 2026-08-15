# Block Status sender (Android)

Phone app for **district transmitters only**. Mark houses RED / YELLOW / BLACK / GREEN and send the same Meshtastic text packets as the laptop **Block Status** app.

It does **not** receive, reconcile heartbeats, or edit districts/precincts. EOC still runs full Block Status on a laptop.

The home-screen name is **Block Status**. The mesh channel is still **`charcStatus`**. Do not run this app and a laptop transmitter on the same precinct at the same time.

## Download

Sideload the field **mesh** APK from **[GitHub Releases](https://github.com/rellefsen/charcTool/releases/latest)** (`BlockStatus-mesh-debug-*.apk`). Allow unknown sources. This is a debug build for field trials, not a Play Store app.

## What it sends

Same wire format as `packet_codec.py`:

```
NS:SOUTH01:H014:Y
NS:SOUTH01:H020:K
NS:SOUTH01:B:H001R,H014Y,H020K
NS:SOUTH01:HB:S
NS:SOUTH01:C:H003G
NS:SOUTH01:HB:E
```

- **Send** — houses that differ from the last successful send
- **Heartbeat** — all non-green houses plus recent GREEN clears, wrapped in `HB:S` / `HB:E`

Default pause between packets is 2 seconds (change it under **Setup**). Payloads stay within the 233-byte Meshtastic text limit.

## Day-of use

The house board is the main screen. Import, BLE scan, mock toggle, and delay live under **Setup**.

1. **Import seed** — zip with the same org/precinct files as the laptop (see below).
2. Pick the **precinct** (top row).
3. Open **Setup**, turn **Mock** off.
4. **Scan radios**, tap the Meshtastic node you want, then **Connect**.
5. Pick the **channel** by name. If the radio has `charcStatus`, the app selects it automatically.
6. Mark houses R / Y / K / G (`K` is BLACK), then **Send**. Use **Heartbeat** when EOC needs a full non-green snapshot.

Pair the radio in Android Bluetooth, then **disconnect** (stay paired). Close the official Meshtastic Android app while this app holds BLE — one client at a time.

If connect fails, something else still owns the BLE slot (Meshtastic app, OS “Connected”, or another phone).

## Prerequisites

- Android 8.0+ (API 26)
- Android Studio with JDK 21 (the bundled JBR is fine)
- SDK platforms **35** and **37** (mesh flavor needs compileSdk 37)
- A Meshtastic radio with BLE (optional; mock flavor works without one)
- Seed zip, same files as the laptop

Meshtastic Kotlin SDK (`org.meshtastic:sdk-*` 0.1.0) is **GPL-3.0**. The **mesh** APK includes it, so a release that uses BLE inherits GPL obligations. The **mock** APK does not.

## Build variants

| Variant | What it is |
|---------|------------|
| `mockDebug` | Default. No Meshtastic SDK. Import, board, and packet generation only. |
| `meshDebug` | BLE via official Meshtastic Kotlin SDK. Install this on the field phone. |

Application id for mock is `org.charctool.sender.mock`, so both APKs can sit on one phone.

### Android Studio

**Build Variants** → `mockDebug` or `meshDebug` → Run or **Build → APK**.

### CLI

From `android-sender/`, with `ANDROID_HOME` and JDK 21 set. This tree may not include `gradlew`; Android Studio or Gradle **9.3.1** both work:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export JAVA_HOME="/opt/android-studio/jbr"   # or your JDK 21
gradle assembleMockDebug    # no radio SDK
gradle assembleMeshDebug    # BLE
```

APKs:

```
app/build/outputs/apk/mock/debug/app-mock-debug.apk
app/build/outputs/apk/mesh/debug/app-mesh-debug.apk
```

Install:

```bash
adb install -r app/build/outputs/apk/mesh/debug/app-mesh-debug.apk
```

### Toolchain that matches the mesh SDK

The Meshtastic SDK 0.1.0 is built with Kotlin 2.4 and requires compileSdk 37.

| Tool | Version |
|------|---------|
| Android Gradle Plugin | 9.1.1 |
| Gradle | 9.3.1 |
| Kotlin (Compose / serialization plugins) | 2.4.0 |
| compileSdk | 37 |
| targetSdk | 35 |
| minSdk | 26 |
| JDK | 21 |

## Seed zip

**Import seed** in Setup. Use the repo zip [`charcTool-housing-data-sample.zip`](../charcTool-housing-data-sample.zip), or any zip with this layout:

```
organization.json
precincts/CHARC01/house_addresses.csv
precincts/CHARC01/neighborhood_status.csv
precincts/SOUTH01/house_addresses.csv
precincts/SOUTH01/neighborhood_status.csv
```

Syntax matches the main README. Status files should start all GREEN.

## Out of scope

- Receiver / EOC board
- Adding or removing districts, precincts, or houses
- USB serial (use the laptop app)
- Holding BLE in the background (keep the app in the foreground while sending)
