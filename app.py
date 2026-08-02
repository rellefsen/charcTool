"""Neighborhood Block Captain — Meshtastic emergency status tool."""

from __future__ import annotations

import logging
from datetime import datetime

import streamlit as st

from config import (
    STATUS_BG,
    STATUS_CODES,
    STATUS_COLORS,
    STATUS_LABELS,
)
from address_store import attach_addresses, init_addresses, read_address_map, update_address
from csv_store import init_csv, read_all, sort_rows_by_urgency, update_status
from house_store import (
    HouseStoreError,
    add_house,
    normalize_house_id,
    remove_house,
    rename_house,
    suggest_next_house_id,
)
from meshtastic_client import MeshtasticClient, list_serial_ports
from packet_codec import encode_bulk_sync_chunks
from receiver import MeshReceiver
from settings_store import SettingsError, load_settings, save_settings
from sync_state import (
    compute_changes_since_baseline,
    compute_sync_rows,
    has_last_sync,
    rows_to_baseline,
    save_last_sync,
    sort_changes_by_urgency,
)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _create_client(settings: dict) -> MeshtasticClient:
    return MeshtasticClient(
        dev_path=settings.get("meshtastic_port"),
        channel_name=str(settings["channel_name"]),
    )


def _sync_packet_delay() -> float:
    return float(st.session_state.app_settings["sync_packet_delay"])


def _init_session_state() -> None:
    init_csv()
    added = ensure_default_houses()
    if added:
        logger.info("Added %s default houses to CSV", added)
    addr_added = ensure_default_addresses()
    if addr_added:
        logger.info("Added %s default addresses", addr_added)
    if "app_settings" not in st.session_state:
        st.session_state.app_settings = load_settings()
    if "client" not in st.session_state:
        st.session_state.client = _create_client(st.session_state.app_settings)
    if "receiver" not in st.session_state:
        st.session_state.receiver = MeshReceiver(st.session_state.client)
    if "mode" not in st.session_state:
        st.session_state.mode = "Transmitter"
    if "pending_edits" not in st.session_state:
        st.session_state.pending_edits = {}
    if "last_sync_log" not in st.session_state:
        st.session_state.last_sync_log = []


# ---------------------------------------------------------------------------
# Page config & accessible styling
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Block Captain Status",
    page_icon="📻",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .main { font-size: 1.15rem; }
        h1 { font-size: 2.4rem !important; }
        h2 { font-size: 1.8rem !important; }
        .stButton > button {
            font-size: 1.4rem !important;
            padding: 0.75rem 2rem !important;
            min-height: 3.5rem !important;
        }
        .status-pill {
            display: inline-block;
            padding: 0.35rem 0.9rem;
            border-radius: 0.5rem;
            font-weight: 700;
            font-size: 1.1rem;
        }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _init_session_state() -> None:
    init_csv()
    init_addresses()
    if "client" not in st.session_state:
        st.session_state.client = MeshtasticClient()
    if "receiver" not in st.session_state:
        st.session_state.receiver = MeshReceiver(st.session_state.client)
    if "mode" not in st.session_state:
        st.session_state.mode = "Transmitter"
    if "pending_edits" not in st.session_state:
        st.session_state.pending_edits = {}
    if "last_sync_log" not in st.session_state:
        st.session_state.last_sync_log = []


def _status_pill(code: str) -> str:
    bg = STATUS_BG.get(code, "#F3F4F6")
    fg = STATUS_COLORS.get(code, "#111827")
    return (
        f'<span class="status-pill" style="background:{bg};color:{fg};">'
        f"{code}</span>"
    )


def _render_connection_banner() -> None:
    info = st.session_state.client.connect()
    if info.mock_mode:
        st.warning(f"📡 {info.message}")
    elif info.channel_index is None:
        st.error(f"📡 {info.message}")
    else:
        st.success(f"📡 {info.message}")


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("Settings")
        st.session_state.mode = st.radio(
            "Operating mode",
            ["Transmitter", "Receiver"],
            index=0 if st.session_state.mode == "Transmitter" else 1,
            help="Transmitter: edit statuses and sync to mesh. "
            "Receiver: listen for incoming updates.",
        )

        st.divider()
        st.subheader("Radio")
        info = st.session_state.client.connection_info()
        if info.mock_mode:
            st.caption("Status: **mock mode** (no radio)")
        elif info.port:
            st.caption(f"Connected: `{info.port}`")
        st.caption(f"Mesh channel: **{info.channel_name}**")
        if info.channel_index is not None:
            st.caption(f"Channel index: {info.channel_index}")

        with st.expander("Radio settings", expanded=False):
            settings = st.session_state.app_settings
            ports = list_serial_ports()
            port_options = ["Auto-detect", *ports]
            current_port = settings.get("meshtastic_port")
            if current_port and current_port not in port_options:
                port_options.append(current_port)
            port_index = (
                port_options.index(current_port)
                if current_port in port_options
                else 0
            )
            selected_port = st.selectbox(
                "Serial port",
                port_options,
                index=port_index,
                help="Choose a specific USB port or auto-detect the radio.",
            )
            channel_name = st.text_input(
                "Mesh channel name",
                value=settings["channel_name"],
                help="Must match a channel configured on the Meshtastic radio.",
            )
            sync_delay = st.number_input(
                "Sync packet delay (seconds)",
                min_value=0.0,
                max_value=30.0,
                step=0.5,
                value=float(settings["sync_packet_delay"]),
                help="Pause between bulk sync packets so LoRa can finish each send.",
            )
            if st.button("Apply & reconnect", use_container_width=True, key="apply_settings"):
                try:
                    new_settings = save_settings(
                        {
                            "meshtastic_port": None
                            if selected_port == "Auto-detect"
                            else selected_port,
                            "channel_name": channel_name,
                            "sync_packet_delay": sync_delay,
                        }
                    )
                    st.session_state.app_settings = new_settings
                    st.session_state.client.close()
                    st.session_state.client = _create_client(new_settings)
                    st.session_state.receiver = MeshReceiver(st.session_state.client)
                    st.toast("Settings saved — reconnecting radio")
                    st.rerun()
                except SettingsError as exc:
                    st.error(str(exc))

        if st.button("Reconnect radio", use_container_width=True):
            st.session_state.client.close()
            st.session_state.client = _create_client(st.session_state.app_settings)
            st.session_state.receiver = MeshReceiver(st.session_state.client)
            st.rerun()

        st.divider()
        st.subheader("Mock testing")
        st.caption(
            "Simulate an incoming mesh packet locally (does not transmit over the radio). "
            "Use in **Receiver** mode to update the board."
        )
        mock_msg = st.text_input("Simulate incoming packet", value="NS:H001:R")
        if st.button("Inject mock packet", use_container_width=True):
            st.session_state.client.mock_inject(mock_msg)
            st.toast(f"Injected: {mock_msg}")

        st.divider()
        st.subheader("Mesh sync")
        if has_last_sync():
            st.caption("Next sync sends **changed houses only**.")
        else:
            st.caption("First sync will send **all houses**.")
        st.session_state.force_full_sync = st.checkbox(
            "Force full sync (all houses)",
            value=st.session_state.get("force_full_sync", False),
        )

        st.divider()
        st.subheader("House management")
        st.caption("Local board only. Addresses are **never transmitted** over the mesh.")
        house_ids = [r["house_id"] for r in read_all()]
        st.caption(f"**{len(house_ids)}** houses on board")

        with st.expander("Add house", expanded=False):
            new_id = st.text_input(
                "House ID",
                value=suggest_next_house_id(),
                key="add_house_id",
                help="1–8 letters or numbers, e.g. H061",
            )
            new_addr = st.text_input(
                "Street address (optional)",
                key="add_house_addr",
            )
            if st.button("Add house", use_container_width=True, key="add_house_btn"):
                try:
                    add_house(new_id, new_addr or None)
                    st.toast(f"Added {normalize_house_id(new_id)}")
                    st.rerun()
                except HouseStoreError as exc:
                    st.error(str(exc))

        if house_ids:
            with st.expander("Edit address", expanded=False):
                edit_house = st.selectbox("Edit address for", house_ids, key="edit_address_house")
                address_map = read_address_map()
                new_address = st.text_input(
                    "Street address",
                    value=address_map.get(edit_house.upper(), ""),
                    key="edit_address_text",
                )
                if st.button("Save address", use_container_width=True):
                    update_address(edit_house, new_address)
                    st.toast(f"Saved address for {edit_house}")
                    st.rerun()

            with st.expander("Rename house", expanded=False):
                rename_from = st.selectbox("House to rename", house_ids, key="rename_from")
                rename_to = st.text_input("New house ID", value=rename_from, key="rename_to")
                if st.button("Rename house", use_container_width=True, key="rename_btn"):
                    try:
                        new_name = normalize_house_id(rename_to)
                        rename_house(rename_from, rename_to)
                        st.toast(f"Renamed {rename_from} → {new_name}")
                        st.rerun()
                    except HouseStoreError as exc:
                        st.error(str(exc))

            with st.expander("Remove house", expanded=False):
                remove_id = st.selectbox("House to remove", house_ids, key="remove_house")
                st.warning(
                    f"Permanently removes **{remove_id}** from status, address, and sync baseline."
                )
                if st.button("Remove house", use_container_width=True, key="remove_btn"):
                    try:
                        remove_house(remove_id)
                        st.toast(f"Removed {remove_id}")
                        st.rerun()
                    except HouseStoreError as exc:
                        st.error(str(exc))


def _render_status_table(rows: list[dict]) -> None:
    st.subheader("Neighborhood Status Board")
    rows = attach_addresses(rows)

    header = st.columns([1.5, 2.5, 2, 2.5, 1.5])
    header[0].markdown("**House**")
    header[1].markdown("**Address**")
    header[2].markdown("**Current Status**")
    header[3].markdown("**Change Status**")
    header[4].markdown("**Last Updated**")

    for row in rows:
        house_id = row["house_id"]
        current = row["status_code"]
        ts = row.get("timestamp", "")
        address = row.get("address", "—")

        cols = st.columns([1.5, 2.5, 2, 2.5, 1.5])
        cols[0].markdown(f"### {house_id}")
        cols[1].markdown(f"**{address}**")
        cols[2].markdown(_status_pill(current), unsafe_allow_html=True)

        selected = cols[3].selectbox(
            f"Status for {house_id}",
            STATUS_CODES,
            index=STATUS_CODES.index(current),
            format_func=lambda c: STATUS_LABELS[c],
            key=f"sel_{house_id}",
            label_visibility="collapsed",
        )
        if selected != current:
            st.session_state.pending_edits[house_id] = selected

        try:
            display_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime(
                "%m/%d %H:%M"
            )
        except ValueError:
            display_ts = ts
        cols[4].caption(display_ts)


def _render_readonly_board(
    rows: list[dict],
    pending_house_ids: set[str] | None = None,
) -> None:
    """Read-only neighborhood board for Receiver mode (no edits, all houses)."""
    rows = attach_addresses(rows)
    pending_house_ids = pending_house_ids or set()

    header = st.columns([1.5, 2.5, 2, 1.5, 1.5])
    header[0].markdown("**House**")
    header[1].markdown("**Address**")
    header[2].markdown("**Status**")
    header[3].markdown("**Updated**")
    header[4].markdown("**Changed**")

    for row in rows:
        house_id = row["house_id"]
        current = row["status_code"]
        ts = row.get("timestamp", "")
        address = row.get("address", "—")
        changed = house_id.upper() in pending_house_ids

        cols = st.columns([1.5, 2.5, 2, 1.5, 1.5])
        cols[0].markdown(f"### {house_id}")
        cols[1].markdown(address)
        cols[2].markdown(_status_pill(current), unsafe_allow_html=True)

        try:
            display_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime(
                "%m/%d %H:%M"
            )
        except ValueError:
            display_ts = ts
        cols[3].caption(display_ts)
        cols[4].markdown("**Yes**" if changed else "—")


def _apply_pending_edits() -> None:
    for house_id, status in st.session_state.pending_edits.items():
        update_status(house_id, status)
    if st.session_state.pending_edits:
        st.session_state.pending_edits.clear()


def _render_transmitter_mode() -> None:
    st.title("📤 Transmitter Mode")
    st.caption("Update house statuses and sync to the mesh network.")
    st.caption("Sorted by urgency: RED first, then YELLOW, then GREEN.")
    if has_last_sync():
        st.caption("Only changed houses are sent after the first successful sync.")
    else:
        st.caption("First sync transmits the full neighborhood board.")

    rows = sort_rows_by_urgency(read_all())
    _render_status_table(rows)

    st.divider()

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("💾 Save Local Changes", use_container_width=True, type="secondary"):
            _apply_pending_edits()
            st.success("Saved to local CSV.")
            st.rerun()

    with col2:
        sync_clicked = st.button(
            "🔄 SYNC TO MESH",
            use_container_width=True,
            type="primary",
        )

    if sync_clicked:
        _apply_pending_edits()
        rows = read_all()
        force_full = st.session_state.get("force_full_sync", False)
        sync_rows, sync_mode = compute_sync_rows(rows, force_full=force_full)

        if sync_mode == "none":
            st.info("Nothing to sync — no house statuses have changed since last mesh sync.")
            st.rerun()

        try:
            packets = encode_bulk_sync_chunks(sync_rows)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

        mode_label = "Full sync" if sync_mode == "full" else "Delta sync"
        total_packets = len(packets)
        packet_delay = _sync_packet_delay()
        est_seconds = max(0, (total_packets - 1) * packet_delay)

        with st.status(
            f"{mode_label}: sending {len(sync_rows)} house(s) in {total_packets} packet(s)…",
            expanded=True,
        ) as sync_status:
            if total_packets > 1:
                st.caption(
                    f"Estimated time: ~{est_seconds:.0f}s "
                    f"({packet_delay:g}s pause between packets for LoRa airtime)"
                )

            progress = st.progress(0.0, text="Starting mesh sync…")
            detail = st.empty()

            def _on_progress(current: int, total: int) -> None:
                fraction = current / total
                progress.progress(
                    fraction,
                    text=f"Sending packet {current} of {total}…",
                )
                detail.markdown(
                    f"**Packet {current}/{total}** — `{packets[current - 1][:80]}"
                    + ("…" if len(packets[current - 1]) > 80 else "")
                    + "`"
                )

            def _on_waiting(current: int, total: int, delay: float) -> None:
                progress.progress(
                    current / total,
                    text=f"Waiting {delay:.0f}s for radio airtime…",
                )
                detail.markdown(
                    f"Packet **{current}/{total}** sent. "
                    f"Pausing **{delay:.0f}s** before packet **{current + 1}**."
                )

            success, errors = st.session_state.client.send_many(
                packets,
                delay_seconds=packet_delay,
                on_progress=_on_progress,
                on_waiting=_on_waiting if total_packets > 1 else None,
            )

            if errors:
                sync_status.update(label="Sync finished with errors", state="error")
                progress.progress(1.0, text="Sync finished with errors")
            else:
                sync_status.update(label="Sync complete", state="complete")
                progress.progress(1.0, text="All packets sent")

        st.session_state.last_sync_log = [
            f"{mode_label}: {len(sync_rows)} house(s) in {total_packets} packet(s)",
            *[f"  [{i + 1}] {pkt}" for i, pkt in enumerate(packets)],
        ] + errors

        if errors:
            st.error(f"Sent {success}/{total_packets} packets — some failed.")
        elif total_packets == 1:
            st.success(
                f"{mode_label}: sent {len(sync_rows)} house status(es) in one mesh packet!"
            )
        else:
            st.success(
                f"{mode_label}: sent {len(sync_rows)} house status(es) "
                f"across {total_packets} mesh packets!"
            )

        if success == total_packets:
            save_last_sync(rows)
            st.session_state.force_full_sync = False

        st.rerun()

    if st.session_state.last_sync_log:
        with st.expander("Last sync log"):
            for line in st.session_state.last_sync_log:
                st.write(line)


def _ensure_receiver_baseline() -> None:
    if "receiver_baseline" not in st.session_state:
        st.session_state.receiver_baseline = rows_to_baseline(read_all())


def _reset_receiver_baseline() -> None:
    st.session_state.receiver_baseline = rows_to_baseline(read_all())


def _format_change_time(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%m/%d %H:%M")
    except ValueError:
        return ts or "—"


def _render_receiver_mode() -> None:
    st.title("📥 Receiver Mode")
    st.caption("Listening for mesh updates and refreshing the status board.")
    _ensure_receiver_baseline()

    receiver: MeshReceiver = st.session_state.receiver
    if not receiver.stats.running:
        receiver.start()

    stats = receiver.stats
    rows = sort_rows_by_urgency(read_all())
    baseline = st.session_state.receiver_baseline
    pending = sort_changes_by_urgency(
        compute_changes_since_baseline(rows, baseline)
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Packets received", stats.packets_received)
    m2.metric("Updates applied", stats.updates_applied)
    m3.metric("Pending changes", len(pending))
    m4.metric("Listener", "Active" if stats.running else "Stopped")

    if stats.last_update:
        st.info(f"Last mesh update: **{stats.last_update}**")
    if stats.last_packet and not stats.last_update:
        st.caption(f"Last packet (non-status): `{stats.last_packet}`")
    if stats.last_error:
        st.error(f"Error: {stats.last_error}")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("✓ Mark all reviewed", use_container_width=True, type="primary"):
            _reset_receiver_baseline()
            st.toast("Baseline updated — pending changes cleared.")
            st.rerun()
    with col_b:
        if st.button("↺ Reset watch baseline", use_container_width=True):
            _reset_receiver_baseline()
            st.toast("Watching for new changes from this board state.")
            st.rerun()

    st.divider()

    pending_ids = {c.house_id.upper() for c in pending}
    if pending:
        st.subheader(f"Changes since watch started ({len(pending)})")
        st.caption(
            "Updated houses are marked **Changed** in the board below. "
            "Addresses are local only and never sent over the mesh."
        )
    else:
        st.success("No pending changes — board matches your watch baseline.")

    if stats.recent_activity:
        with st.expander("Recent mesh activity", expanded=bool(pending)):
            for item in list(stats.recent_activity)[:8]:
                st.markdown(
                    f"**{_format_change_time(item.at)}** — {item.summary}  \n"
                    f"<span style='color:#6B7280;font-size:0.9rem'>`{item.packet}`</span>",
                    unsafe_allow_html=True,
                )

    st.divider()
    st.subheader(f"Neighborhood board ({len(rows)} houses)")
    st.caption("Read-only. Sorted by urgency: RED first, then YELLOW, then GREEN.")
    _render_readonly_board(rows, pending_ids)

    counts = {code: sum(1 for r in rows if r["status_code"] == code) for code in STATUS_CODES}
    c1, c2, c3 = st.columns(3)
    c1.metric("🔴 RED", counts.get("RED", 0))
    c2.metric("🟡 YELLOW", counts.get("YELLOW", 0))
    c3.metric("🟢 GREEN", counts.get("GREEN", 0))

    st.caption("Auto-refreshing every 2 seconds…")
    import time

    time.sleep(2)
    st.rerun()


def main() -> None:
    _init_session_state()
    _render_connection_banner()
    _render_sidebar()

    if st.session_state.mode == "Transmitter":
        if st.session_state.receiver.stats.running:
            st.session_state.receiver.stop()
        st.session_state._last_mode = "Transmitter"
        _render_transmitter_mode()
    else:
        if st.session_state.get("_last_mode") != "Receiver":
            st.session_state.pop("receiver_baseline", None)
        st.session_state._last_mode = "Receiver"
        _render_receiver_mode()


if __name__ == "__main__":
    main()
