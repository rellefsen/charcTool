"""Standalone Meshtastic field checklist HTML for in-app popup / print."""

from __future__ import annotations

import html as html_lib

from config import HEARTBEAT_INTERVAL_SECONDS, MESH_MAX_PAYLOAD_BYTES, SYNC_PACKET_DELAY


def build_field_checklist_html(
    *,
    channel_name: str,
    packet_delay: float = SYNC_PACKET_DELAY,
    heartbeat_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
) -> str:
    """Build a self-contained HTML checklist for field radio standup."""
    channel = html_lib.escape(channel_name.strip() or "charcStatus")
    delay_label = f"{packet_delay:g} s"
    heartbeat_minutes = max(1, int(round(heartbeat_seconds / 60.0)))
    payload = f"{MESH_MAX_PAYLOAD_BYTES} B"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Block Status field checklist</title>
<style>
  body {{
    font-family: Arial, Helvetica, sans-serif;
    color: #111827;
    margin: 0.75in;
    font-size: 12pt;
    line-height: 1.45;
  }}
  h1 {{ font-size: 20pt; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 13pt; margin: 1.4rem 0 0.5rem; }}
  .meta {{ color: #4b5563; margin-bottom: 1rem; }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.5rem;
    margin: 1rem 0;
  }}
  .stat {{
    border: 1px solid #d1d5db;
    padding: 0.5rem 0.6rem;
    background: #f9fafb;
  }}
  .stat strong {{ display: block; font-size: 12pt; }}
  .stat span {{ color: #4b5563; font-size: 9pt; }}
  .warn {{
    border: 1px solid #f59e0b;
    background: #fffbeb;
    padding: 0.6rem 0.75rem;
    margin: 0.75rem 0 1rem;
  }}
  table {{ width: 100%; border-collapse: collapse; margin: 0.4rem 0 1rem; }}
  th, td {{
    border: 1px solid #d1d5db;
    padding: 0.35rem 0.5rem;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #f3f4f6;
    font-size: 10pt;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }}
  tr {{ page-break-inside: avoid; }}
  ol {{ margin: 0.3rem 0 1rem 1.25rem; padding: 0; }}
  li {{ margin: 0.25rem 0; }}
  code {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 10.5pt;
    background: #f3f4f6;
    padding: 0.05rem 0.3rem;
  }}
  .print-bar {{ margin-bottom: 1rem; }}
  button {{
    font-size: 1rem;
    padding: 0.4rem 0.9rem;
    cursor: pointer;
  }}
  @media print {{
    body {{ margin: 0.5in; }}
    .print-bar {{ display: none; }}
  }}
</style>
</head>
<body>
  <div class="print-bar"><button onclick="window.print()">Print checklist</button></div>
  <h1>Block Status field checklist</h1>
  <div class="meta">
    Radio stack is Meshtastic only. Block Status sends UTF-8 text on a named channel.
    Path hash, companions, and MeshCore firmware are out of scope.
  </div>
  <div class="stats">
    <div class="stat"><strong>{channel}</strong><span>Required channel name</span></div>
    <div class="stat"><strong>{payload}</strong><span>Max text payload</span></div>
    <div class="stat"><strong>{delay_label}</strong><span>Packet delay (this node)</span></div>
    <div class="stat"><strong>{heartbeat_minutes} min</strong><span>Heartbeat interval</span></div>
  </div>
  <div class="warn">
    <strong>Do not go live in mock mode.</strong>
    If Streamlit says mock mode, no packets leave the laptop. Fix USB / serial
    permissions, or set the port in the sidebar, then reconnect.
  </div>

  <h2>Who runs what</h2>
  <table>
    <thead><tr><th>Site</th><th>Radio role</th><th>Laptop role</th><th>Must match</th></tr></thead>
    <tbody>
      <tr><td>Each district</td><td>CLIENT USB radio</td><td>Transmitter — status + heartbeat</td><td>{channel} + same seed CSVs</td></tr>
      <tr><td>Coverage (roofs)</td><td>ROUTER / ROUTER_LATE</td><td>No laptop required</td><td>Same modem + hop limit</td></tr>
      <tr><td>EOC</td><td>CLIENT USB radio</td><td>Receiver — apply NS: packets</td><td>{channel} + same seed CSVs</td></tr>
    </tbody>
  </table>

  <h2>Radio settings (Meshtastic app / CLI)</h2>
  <p>Block Status does not write hop limit, PSK, or modem preset. Those live on the radio. The app only looks up the channel named in settings.</p>
  <table>
    <thead><tr><th>Setting</th><th>Block Status default</th><th>Field rule</th></tr></thead>
    <tbody>
      <tr><td>Channel name</td><td><code>{channel}</code></td><td>Exact match on every radio. Missing name = connected but cannot send.</td></tr>
      <tr><td>Channel PSK</td><td>Set on radio</td><td>Identical secret on every node. Name match is not enough.</td></tr>
      <tr><td>Payload size</td><td>{payload} UTF-8</td><td>App chunks bulk/heartbeat packets. Do not shrink payload via modem changes without testing.</td></tr>
      <tr><td>wantAck</td><td>Off (False)</td><td>Link ACKs are disabled. Reliability is delta sync + hourly heartbeat.</td></tr>
      <tr><td>Hop limit</td><td>Radio default (~3)</td><td>Raise if districts are more than a few hops from EOC. Start at 5 for city coverage.</td></tr>
      <tr><td>Packet delay</td><td>{delay_label}</td><td>Raise toward 4–6 s if LoRa is congested or packets vanish. Cap is 30 s.</td></tr>
      <tr><td>Serial port</td><td>Auto-detect</td><td>If two USB radios are plugged in, pick the port in the sidebar.</td></tr>
      <tr><td>Bluetooth</td><td>Optional</td><td>Pair in OS first, then Radio settings → Bluetooth → Scan. Close the phone app; only one BLE client at a time.</td></tr>
    </tbody>
  </table>

  <h2>1. Radios</h2>
  <ol>
    <li>Flash current Meshtastic firmware on every radio (district + EOC).</li>
    <li>Set unique long names: EOC-OPS, SOUTH-TX, NORTH-TX, etc.</li>
    <li>Create a secondary channel named <code>{channel}</code> on every radio. Same PSK.</li>
    <li>Leave Primary as admin/chat if you want; Block Status only uses <code>{channel}</code>.</li>
    <li>Set hop limit high enough for district → city routers → EOC (start at 5).</li>
    <li>Match modem preset on all nodes (LongFast is the usual default).</li>
    <li>District + EOC laptops: CLIENT. Roof/coverage nodes: ROUTER or ROUTER_LATE.</li>
    <li>Confirm the radio link: USB <code>meshtastic --info</code>, or Bluetooth <code>meshtastic --ble-scan</code> then <code>meshtastic --ble --info</code>.</li>
  </ol>

  <h2>2. Seed every laptop</h2>
  <p>No over-air org/address export. Copy files by USB stick before the event.</p>
  <ol>
    <li>Copy the same <code>data/organization.json</code> to every laptop.</li>
    <li>Copy each precinct folder: <code>house_addresses.csv</code> + <code>neighborhood_status.csv</code> (all GREEN).</li>
    <li>District nodes: Transmitter view, heartbeat on, interval {heartbeat_minutes} minutes.</li>
    <li>EOC laptop: Receiver view, watching the districts you expect to hear.</li>
  </ol>

  <h2>3. Live smoke test (one district → EOC)</h2>
  <ol>
    <li>Connect radio. Status must say connected, not mock mode.</li>
    <li>Confirm channel index is shown for <code>{channel}</code> (not missing).</li>
    <li>Send a short free-form text; EOC should see it in recent activity.</li>
    <li>Mark one house YELLOW, SYNC TO MESH; EOC board updates.</li>
    <li>Clear that house to GREEN; EOC should follow (or catch it on heartbeat).</li>
    <li>Send heartbeat now from the district; EOC shows last heartbeat for that precinct.</li>
  </ol>

  <h2>What flies over the air</h2>
  <table>
    <thead><tr><th>When</th><th>What</th><th>Example</th></tr></thead>
    <tbody>
      <tr><td>Operator commit</td><td>SYNC TO MESH sends only changed houses as <code>NS:</code> packets.</td><td><code>NS:SOUTH01:H014:Y</code></td></tr>
      <tr><td>Hourly heartbeat</td><td>HB:S, all non-green houses, optional recent GREEN clears, then HB:E. EOC sets missing RED/YELLOW houses back to GREEN.</td><td><code>NS:SOUTH01:HB:S</code> … <code>NS:SOUTH01:B:H001R,H014Y</code> … <code>NS:SOUTH01:HB:E</code></td></tr>
    </tbody>
  </table>

  <h2>If it fails</h2>
  <table>
    <thead><tr><th>Symptom</th><th>Likely cause</th><th>Fix</th></tr></thead>
    <tbody>
      <tr><td>Status: mock mode</td><td>No serial radio, or Python cannot open the port</td><td>Unplug/replug USB, install drivers, pick port, reconnect.</td></tr>
      <tr><td>Connected, channel not found</td><td>Radio has no channel named {channel}</td><td>Add the channel in Meshtastic, same PSK, reconnect Block Status.</td></tr>
      <tr><td>EOC never updates</td><td>Different channel index/PSK, or hop limit too low</td><td>Send a plain text first. If text fails, it is radio config, not the CSV.</td></tr>
      <tr><td>Only some packets arrive</td><td>Airtime collision / delay too short</td><td>Raise sync delay to 4–6 s. Keep heartbeats on.</td></tr>
      <tr><td>EOC stuck RED after a clear</td><td>Missed GREEN delta</td><td>Send heartbeat now. HB:E reconciles houses not in the non-green snapshot.</td></tr>
      <tr><td>Two radios, wrong one used</td><td>Auto-detect refuses to guess</td><td>Set serial port in sidebar; leave the other radio unplugged if unsure.</td></tr>
    </tbody>
  </table>
</body>
</html>"""
