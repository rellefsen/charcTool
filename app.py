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
    STATUS_URGENCY,
)
from address_store import attach_addresses, default_address, read_address_map, update_address
from csv_store import ensure_status_csv, read_all, sort_rows_by_urgency, update_status
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
from precinct_store import (
    PrecinctPaths,
    get_district_for_precinct,
    get_precinct,
    init_organization,
    init_precinct_data,
    list_districts,
    list_precincts,
    migrate_legacy_data,
    paths_for_precinct,
    precinct_ids_for_district,
)
from receiver import MeshReceiver
from settings_store import SettingsError, load_settings, save_settings
from sync_state import (
    compute_sync_rows,
    format_status_change,
    has_last_sync,
    save_last_sync,
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


def _active_precinct_id() -> str:
    return str(st.session_state.app_settings["active_precinct_id"]).upper()


def _active_district_id() -> str:
    return str(st.session_state.app_settings["active_district_id"]).upper()


def _active_paths() -> PrecinctPaths:
    return paths_for_precinct(_active_precinct_id())


def _save_context_settings(**updates: object) -> None:
    settings = {**st.session_state.app_settings, **updates}
    st.session_state.app_settings = save_settings(settings)


def _configure_receiver() -> None:
    district_id = _active_district_id()
    precincts = list_precincts(district_id)
    st.session_state.receiver.watched_precinct_ids = precinct_ids_for_district(district_id)
    st.session_state.receiver.legacy_precinct_id = (
        precincts[0].id if len(precincts) == 1 else None
    )


def _read_district_rows(district_id: str) -> list[dict]:
    rows: list[dict] = []
    for precinct in list_precincts(district_id):
        paths = paths_for_precinct(precinct.id)
        paths.status.parent.mkdir(parents=True, exist_ok=True)
        ensure_status_csv(paths.status)
        for row in read_all(path=paths.status):
            rows.append({**row, "precinct_id": precinct.id})
    return sort_rows_by_urgency(rows)


def _district_baseline(rows: list[dict]) -> dict[str, str]:
    return {
        f"{row['precinct_id']}:{row['house_id'].upper()}": row["status_code"]
        for row in rows
    }


def _compute_district_changes(
    rows: list[dict],
    baseline: dict[str, str],
) -> list[dict]:
    changes: list[dict] = []
    for row in rows:
        key = f"{row['precinct_id']}:{row['house_id'].upper()}"
        previous = baseline.get(key)
        current = row["status_code"]
        if previous != current:
            changes.append(
                {
                    "precinct_id": row["precinct_id"],
                    "house_id": row["house_id"],
                    "previous_status": previous,
                    "current_status": current,
                    "timestamp": row.get("timestamp", ""),
                }
            )
    return sorted(
        changes,
        key=lambda c: (
            STATUS_URGENCY.get(c["current_status"], 99),
            c["precinct_id"],
            c["house_id"],
        ),
    )


def _change_labels(pending: list[dict]) -> dict[str, str]:
    return {
        f"{change['precinct_id']}:{change['house_id'].upper()}": format_status_change(
            change.get("previous_status"),
            change["current_status"],
        )
        for change in pending
    }


def _init_session_state() -> None:
    migrate_legacy_data()
    init_organization()
    if "app_settings" not in st.session_state:
        st.session_state.app_settings = load_settings()
    init_precinct_data(st.session_state.app_settings["active_precinct_id"])
    if "client" not in st.session_state:
        st.session_state.client = _create_client(st.session_state.app_settings)
    if "receiver" not in st.session_state:
        st.session_state.receiver = MeshReceiver(st.session_state.client)
    _configure_receiver()
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
        st.subheader("Organization")
        if st.session_state.mode == "Transmitter":
            precincts = list_precincts()
            precinct_labels = {p.id: f"{p.id} — {p.name}" for p in precincts}
            current_precinct = _active_precinct_id()
            precinct_ids = [p.id for p in precincts]
            selected_precinct = st.selectbox(
                "Active precinct",
                precinct_ids,
                index=precinct_ids.index(current_precinct)
                if current_precinct in precinct_ids
                else 0,
                format_func=lambda pid: precinct_labels.get(pid, pid),
                help="Each precinct has its own local status and address CSV files.",
            )
            if selected_precinct != current_precinct:
                _save_context_settings(
                    active_precinct_id=selected_precinct,
                    active_district_id=get_district_for_precinct(selected_precinct),
                )
                st.session_state.pending_edits.clear()
                init_precinct_data(selected_precinct)
                _configure_receiver()
                st.rerun()
        else:
            districts = list_districts()
            district_labels = {d.id: f"{d.id} — {d.name}" for d in districts}
            current_district = _active_district_id()
            district_ids = [d.id for d in districts]
            selected_district = st.selectbox(
                "Active district",
                district_ids,
                index=district_ids.index(current_district)
                if current_district in district_ids
                else 0,
                format_func=lambda did: district_labels.get(did, did),
                help="Receiver aggregates all precinct CSVs in the selected district.",
            )
            if selected_district != current_district:
                _save_context_settings(active_district_id=selected_district)
                st.session_state.pop("receiver_baseline", None)
                _configure_receiver()
                st.rerun()
            watched = list_precincts(selected_district)
            st.caption(
                f"Listening for **{len(watched)}** precinct(s): "
                + ", ".join(p.id for p in watched)
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
                    _configure_receiver()
                    st.toast("Settings saved — reconnecting radio")
                    st.rerun()
                except SettingsError as exc:
                    st.error(str(exc))

        if st.button("Reconnect radio", use_container_width=True):
            st.session_state.client.close()
            st.session_state.client = _create_client(st.session_state.app_settings)
            st.session_state.receiver = MeshReceiver(st.session_state.client)
            _configure_receiver()
            st.rerun()

        st.divider()
        st.subheader("Mock testing")
        st.caption(
            "Simulate an incoming mesh packet locally (does not transmit over the radio). "
            "Use in **Receiver** mode to update the board."
        )
        mock_msg = st.text_input(
            "Simulate incoming packet",
            value=f"NS:{_active_precinct_id()}:H001:R",
        )
        if st.button("Inject mock packet", use_container_width=True):
            st.session_state.client.mock_inject(mock_msg)
            st.toast(f"Injected: {mock_msg}")

        st.divider()
        st.subheader("Mesh sync")
        paths = _active_paths()
        if has_last_sync(path=paths.last_sync):
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
        paths = _active_paths()
        house_ids = [r["house_id"] for r in read_all(path=paths.status)]
        st.caption(f"**{len(house_ids)}** houses in **{_active_precinct_id()}**")

        with st.expander("Add house", expanded=False):
            new_id = st.text_input(
                "House ID",
                value=suggest_next_house_id(paths),
                key="add_house_id",
                help="1–8 letters or numbers, e.g. H061",
            )
            new_addr = st.text_input(
                "Street address (optional)",
                key="add_house_addr",
            )
            if st.button("Add house", use_container_width=True, key="add_house_btn"):
                try:
                    add_house(new_id, paths, new_addr or None)
                    st.toast(f"Added {normalize_house_id(new_id)}")
                    st.rerun()
                except HouseStoreError as exc:
                    st.error(str(exc))

        if house_ids:
            with st.expander("Edit address", expanded=False):
                edit_house = st.selectbox("Edit address for", house_ids, key="edit_address_house")
                address_map = read_address_map(path=paths.addresses)
                new_address = st.text_input(
                    "Street address",
                    value=address_map.get(edit_house.upper(), ""),
                    key="edit_address_text",
                )
                if st.button("Save address", use_container_width=True):
                    update_address(edit_house, new_address, path=paths.addresses)
                    st.toast(f"Saved address for {edit_house}")
                    st.rerun()

            with st.expander("Rename house", expanded=False):
                rename_from = st.selectbox("House to rename", house_ids, key="rename_from")
                rename_to = st.text_input("New house ID", value=rename_from, key="rename_to")
                if st.button("Rename house", use_container_width=True, key="rename_btn"):
                    try:
                        new_name = normalize_house_id(rename_to)
                        rename_house(rename_from, rename_to, paths)
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
                        remove_house(remove_id, paths)
                        st.toast(f"Removed {remove_id}")
                        st.rerun()
                    except HouseStoreError as exc:
                        st.error(str(exc))


def _render_status_table(rows: list[dict], paths: PrecinctPaths) -> None:
    st.subheader("Neighborhood Status Board")
    rows = attach_addresses(rows, path=paths.addresses)

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


def _attach_district_addresses(rows: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for row in rows:
        paths = paths_for_precinct(row["precinct_id"])
        address_map = read_address_map(path=paths.addresses)
        enriched.append(
            {
                **row,
                "address": address_map.get(
                    row["house_id"].upper(),
                    default_address(row["house_id"]),
                ),
            }
        )
    return enriched


def _render_readonly_board(
    rows: list[dict],
    paths: PrecinctPaths | None = None,
    change_labels: dict[str, str] | None = None,
    show_precinct: bool = False,
) -> None:
    """Read-only neighborhood board for Receiver mode (no edits, all houses)."""
    if show_precinct:
        rows = _attach_district_addresses(rows)
    elif paths is not None:
        rows = attach_addresses(rows, path=paths.addresses)
    else:
        rows = attach_addresses(rows)
    change_labels = change_labels or {}

    if show_precinct:
        header = st.columns([1.2, 1.2, 2.2, 2, 1.5, 1.2])
        header[0].markdown("**Precinct**")
        header[1].markdown("**House**")
        header[2].markdown("**Address**")
        header[3].markdown("**Status**")
        header[4].markdown("**Updated**")
        header[5].markdown("**Was → Now**")
        col_weights = [1.2, 1.2, 2.2, 2, 1.5, 1.5]
    else:
        header = st.columns([1.5, 2.5, 2, 1.5, 1.5])
        header[0].markdown("**House**")
        header[1].markdown("**Address**")
        header[2].markdown("**Status**")
        header[3].markdown("**Updated**")
        header[4].markdown("**Was → Now**")
        col_weights = [1.5, 2.5, 2, 1.5, 1.5]

    for row in rows:
        house_id = row["house_id"]
        current = row["status_code"]
        ts = row.get("timestamp", "")
        address = row.get("address", "—")
        precinct_id = row.get("precinct_id", "")
        change_key = (
            f"{precinct_id}:{house_id.upper()}" if show_precinct else house_id.upper()
        )
        change_label = change_labels.get(change_key, "")

        cols = st.columns(col_weights)
        offset = 0
        if show_precinct:
            cols[0].markdown(f"**{precinct_id}**")
            offset = 1
        cols[offset].markdown(f"### {house_id}")
        cols[offset + 1].markdown(address)
        cols[offset + 2].markdown(_status_pill(current), unsafe_allow_html=True)

        try:
            display_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime(
                "%m/%d %H:%M"
            )
        except ValueError:
            display_ts = ts
        cols[offset + 3].caption(display_ts)
        if change_label:
            cols[offset + 4].markdown(f"**{change_label}**")
        else:
            cols[offset + 4].markdown("—")


def _apply_pending_edits(paths: PrecinctPaths) -> None:
    for house_id, status in st.session_state.pending_edits.items():
        update_status(house_id, status, path=paths.status)
    if st.session_state.pending_edits:
        st.session_state.pending_edits.clear()


def _render_transmitter_mode() -> None:
    paths = _active_paths()
    precinct = get_precinct(_active_precinct_id())
    precinct_label = f"{precinct.id} — {precinct.name}" if precinct else _active_precinct_id()

    st.title("📤 Transmitter Mode")
    st.caption(f"Precinct: **{precinct_label}**")
    st.caption("Update house statuses and sync to the mesh network.")
    st.caption("Sorted by urgency: RED first, then YELLOW, then GREEN.")
    if has_last_sync(path=paths.last_sync):
        st.caption("Only changed houses are sent after the first successful sync.")
    else:
        st.caption("First sync transmits the full neighborhood board.")

    rows = sort_rows_by_urgency(read_all(path=paths.status))
    _render_status_table(rows, paths)

    st.divider()

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("💾 Save Local Changes", use_container_width=True, type="secondary"):
            _apply_pending_edits(paths)
            st.success("Saved to local CSV.")
            st.rerun()

    with col2:
        sync_clicked = st.button(
            "🔄 SYNC TO MESH",
            use_container_width=True,
            type="primary",
        )

    if sync_clicked:
        _apply_pending_edits(paths)
        rows = read_all(path=paths.status)
        force_full = st.session_state.get("force_full_sync", False)
        sync_rows, sync_mode = compute_sync_rows(
            rows,
            force_full=force_full,
            last_sync_path=paths.last_sync,
        )

        if sync_mode == "none":
            st.info("Nothing to sync — no house statuses have changed since last mesh sync.")
            st.rerun()

        try:
            packets = encode_bulk_sync_chunks(_active_precinct_id(), sync_rows)
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
            save_last_sync(rows, path=paths.last_sync)
            st.session_state.force_full_sync = False

        st.rerun()

    if st.session_state.last_sync_log:
        with st.expander("Last sync log"):
            for line in st.session_state.last_sync_log:
                st.write(line)


def _ensure_receiver_baseline(rows: list[dict]) -> None:
    if "receiver_baseline" not in st.session_state:
        st.session_state.receiver_baseline = _district_baseline(rows)


def _reset_receiver_baseline(rows: list[dict]) -> None:
    st.session_state.receiver_baseline = _district_baseline(rows)


def _format_change_time(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%m/%d %H:%M")
    except ValueError:
        return ts or "—"


def _render_receiver_mode() -> None:
    district_id = _active_district_id()
    districts = {d.id: d.name for d in list_districts()}
    district_label = f"{district_id} — {districts.get(district_id, district_id)}"

    st.title("📥 Receiver Mode")
    st.caption(f"District: **{district_label}**")
    st.caption("Listening for mesh updates and refreshing the status board.")

    receiver: MeshReceiver = st.session_state.receiver
    if not receiver.stats.running:
        receiver.start()

    stats = receiver.stats
    rows = _read_district_rows(district_id)
    _ensure_receiver_baseline(rows)
    baseline = st.session_state.receiver_baseline
    pending = _compute_district_changes(rows, baseline)

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
            _reset_receiver_baseline(rows)
            st.toast("Baseline updated — pending changes cleared.")
            st.rerun()
    with col_b:
        if st.button("↺ Reset watch baseline", use_container_width=True):
            _reset_receiver_baseline(rows)
            st.toast("Watching for new changes from this board state.")
            st.rerun()

    st.divider()

    change_labels = _change_labels(pending)
    if pending:
        st.subheader(f"Changes since watch started ({len(pending)})")
        st.caption(
            "Status transitions are shown in the **Was → Now** column below. "
            "Addresses are local only and never sent over the mesh."
        )
        for change in pending:
            label = format_status_change(
                change.get("previous_status"),
                change["current_status"],
            )
            st.markdown(f"**{change['precinct_id']}/{change['house_id']}** — {label}")
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
    st.subheader(f"District board ({len(rows)} houses)")
    st.caption("Read-only. Sorted by urgency: RED first, then YELLOW, then GREEN.")
    _render_readonly_board(rows, change_labels=change_labels, show_precinct=True)

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
