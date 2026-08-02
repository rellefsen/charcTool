"""Neighborhood Block Captain — Meshtastic emergency status tool."""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

from config import (
    STATUS_BG,
    STATUS_CODES,
    STATUS_COLORS,
    STATUS_LABELS,
    STATUS_URGENCY,
    MESH_MAX_PAYLOAD_BYTES,
)
from address_store import (
    AddressStoreError,
    attach_addresses,
    default_address,
    import_addresses,
    parse_address_csv,
    read_address_map,
    update_address,
)
from csv_store import ensure_status_csv, read_all, sort_rows_by_urgency, update_status
from house_store import (
    HouseStoreError,
    add_house,
    normalize_house_id,
    remove_house,
    rename_house,
    suggest_next_house_id,
)
from meshtastic_client import (
    MeshtasticClient,
    _ensure_global_pubsub,
    _set_active_client,
    list_serial_ports,
)
from packet_codec import encode_bulk_sync_chunks
from print_board import build_printable_html
from precinct_store import (
    PrecinctPaths,
    PrecinctStoreError,
    add_district,
    add_precinct,
    get_district_for_precinct,
    get_precinct,
    init_organization,
    init_precinct_data,
    list_districts,
    list_precincts,
    make_precinct_id,
    migrate_legacy_data,
    paths_for_precinct,
    precinct_ids_for_district,
    remove_district,
    remove_precinct,
    suggest_next_precinct_suffix,
)
from receiver import MeshReceiver
from settings_store import SettingsError, load_settings, save_settings
from sync_state import (
    compute_sync_rows,
    format_status_change,
    has_last_sync,
    save_last_sync,
)
from text_messages import (
    TextMessageError,
    drain_pending,
    format_message_text,
    record_received,
    record_sent,
    validate_message,
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


def _switch_context_after_precinct_removed(removed_id: str) -> None:
    if _active_precinct_id() != removed_id:
        _configure_receiver()
        return

    remaining = list_precincts()
    fallback = remaining[0]
    _save_context_settings(
        active_precinct_id=fallback.id,
        active_district_id=fallback.district_id,
    )
    st.session_state.pending_edits.clear()
    init_precinct_data(fallback.id)
    _configure_receiver()


def _switch_context_after_district_removed(removed_id: str) -> None:
    if _active_district_id() != removed_id:
        return

    fallback_district = list_districts()[0]
    fallback_precinct = list_precincts(fallback_district.id)[0]
    _save_context_settings(
        active_district_id=fallback_district.id,
        active_precinct_id=fallback_precinct.id,
    )
    st.session_state.pending_edits.clear()
    st.session_state.pop("receiver_baseline", None)
    init_precinct_data(fallback_precinct.id)
    _configure_receiver()


def _configure_receiver() -> None:
    district_id = _active_district_id()
    precincts = list_precincts(district_id)
    st.session_state.receiver.watched_precinct_ids = precinct_ids_for_district(district_id)
    st.session_state.receiver.legacy_precinct_id = (
        precincts[0].id if len(precincts) == 1 else None
    )


def _restart_receiver_if_needed() -> None:
    receiver: MeshReceiver = st.session_state.receiver
    if st.session_state.mode != "Receiver":
        if receiver.stats.running:
            receiver.stop()
        return
    if not receiver.stats.running:
        receiver.start()


def _reconnect_radio(app_settings: dict | None = None) -> None:
    if app_settings is not None:
        st.session_state.app_settings = app_settings

    receiver: MeshReceiver = st.session_state.receiver
    if receiver.stats.running:
        receiver.stop()

    st.session_state.client.close()
    st.session_state.client = _create_client(st.session_state.app_settings)
    st.session_state.receiver = MeshReceiver(st.session_state.client)
    _set_active_client(st.session_state.client)
    _ensure_global_pubsub()
    _register_text_message_listener()
    _configure_receiver()
    st.session_state.client.reconnect()
    _restart_receiver_if_needed()


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


def _on_mesh_text_message(text: str, from_id: str | None = None) -> None:
    record_received(text, from_id=from_id)


def _register_text_message_listener() -> None:
    st.session_state.client.register_receive_callback(_on_mesh_text_message)
    st.session_state.text_message_listener_client = id(st.session_state.client)


def _ensure_text_message_listener() -> None:
    client = st.session_state.client
    if st.session_state.get("text_message_listener_client") != id(client):
        _register_text_message_listener()


def _message_alert_key(item: dict) -> str:
    return f"{item.get('at')}|{item.get('from_id')}|{item.get('text')}"


def _dismiss_text_alert() -> None:
    alert = st.session_state.get("text_message_alert")
    if alert:
        dismissed = list(st.session_state.get("text_message_alert_dismissed_keys", []))
        key = _message_alert_key(alert)
        if key not in dismissed:
            dismissed.append(key)
        st.session_state.text_message_alert_dismissed_keys = dismissed[-50:]
    st.session_state.text_message_alert = None


def _sync_text_messages() -> list[dict]:
    if "text_messages" not in st.session_state:
        st.session_state.text_messages = []

    client = st.session_state.client
    new_received_alert: dict | None = None
    for message in drain_pending():
        sender_name = client.node_display_name(message.from_id)
        item = {
            "at": message.at,
            "direction": message.direction,
            "text": message.text,
            "from_id": message.from_id,
            "sender_name": sender_name,
        }
        st.session_state.text_messages.insert(0, item)
        if message.direction == "received" and new_received_alert is None:
            new_received_alert = item

    if new_received_alert is not None:
        dismissed = st.session_state.get("text_message_alert_dismissed_keys", [])
        if _message_alert_key(new_received_alert) not in dismissed:
            st.session_state.text_message_alert = new_received_alert

    for item in st.session_state.text_messages:
        if item.get("from_id") and not item.get("sender_name"):
            item["sender_name"] = client.node_display_name(item["from_id"])
        elif item.get("from_id") and item["sender_name"] == item["from_id"]:
            resolved = client.node_display_name(item["from_id"])
            if resolved and resolved != item["from_id"]:
                item["sender_name"] = resolved

    st.session_state.text_messages = st.session_state.text_messages[:50]
    return st.session_state.text_messages


def _init_session_state() -> None:
    migrate_legacy_data()
    init_organization()
    if "app_settings" not in st.session_state:
        st.session_state.app_settings = load_settings()
    init_precinct_data(st.session_state.app_settings["active_precinct_id"])
    if "client" not in st.session_state:
        st.session_state.client = _create_client(st.session_state.app_settings)
    _set_active_client(st.session_state.client)
    _ensure_global_pubsub()
    if "text_messages" not in st.session_state:
        st.session_state.text_messages = []
    if "text_message_alert" not in st.session_state:
        st.session_state.text_message_alert = None
    if "text_message_alert_dismissed_keys" not in st.session_state:
        st.session_state.text_message_alert_dismissed_keys = []
    _ensure_text_message_listener()
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
        @keyframes text-msg-flash {
            0%, 100% { background-color: #FEF3C7; border-color: #F59E0B; }
            50% { background-color: #FDE68A; border-color: #D97706; }
        }
        .text-message-alert {
            animation: text-msg-flash 1s ease-in-out infinite;
            border: 3px solid #F59E0B;
            border-radius: 0.5rem;
            padding: 1rem 1.25rem;
            margin-bottom: 1rem;
            font-size: 1.25rem;
            line-height: 1.5;
        }
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


def _render_print_board_actions(
    rows: list[dict],
    *,
    title: str,
    subtitle: str,
    file_stem: str,
    paths: PrecinctPaths | None = None,
    show_precinct: bool = False,
    change_labels: dict[str, str] | None = None,
) -> None:
    if show_precinct:
        display_rows = _attach_district_addresses(rows)
    elif paths is not None:
        display_rows = attach_addresses(rows, path=paths.addresses)
    else:
        display_rows = attach_addresses(rows)

    html = build_printable_html(
        display_rows,
        title=title,
        subtitle=subtitle,
        show_precinct=show_precinct,
        change_labels=change_labels,
    )
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in file_stem)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.download_button(
            "Download printable board",
            data=html,
            file_name=f"{safe_stem}-board.html",
            mime="text/html",
            use_container_width=True,
        )
    with col2:
        components.html(
            f"""
            <button id="print-board-btn" style="
                width: 100%;
                font-size: 1rem;
                padding: 0.55rem 1rem;
                border: 1px solid rgba(49, 51, 63, 0.2);
                border-radius: 0.5rem;
                background: white;
                cursor: pointer;
            ">Print board</button>
            <script>
            document.getElementById("print-board-btn").onclick = function() {{
                var printWindow = window.open("", "_blank");
                printWindow.document.write({json.dumps(html)});
                printWindow.document.close();
                printWindow.onload = function() {{ printWindow.print(); }};
            }};
            </script>
            """,
            height=60,
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

        with st.expander("Add district", expanded=False):
            new_district_id = st.text_input(
                "District ID",
                key="add_district_id",
                help="2–8 letters or numbers, e.g. SOUTH",
            )
            new_district_name = st.text_input(
                "District name",
                key="add_district_name",
                placeholder="South District",
            )
            if st.button("Add district", use_container_width=True, key="add_district_btn"):
                try:
                    district = add_district(new_district_id, new_district_name)
                    st.toast(f"Added district {district.id}")
                    st.rerun()
                except PrecinctStoreError as exc:
                    st.error(str(exc))

        districts = list_districts()
        with st.expander("Add precinct", expanded=False):
            if not districts:
                st.caption("Add a district first.")
            else:
                district_options = {d.id: f"{d.id} — {d.name}" for d in districts}
                add_precinct_district = st.selectbox(
                    "District",
                    list(district_options),
                    format_func=lambda did: district_options[did],
                    key="add_precinct_district",
                )
                suggested_suffix = suggest_next_precinct_suffix(add_precinct_district)
                precinct_suffix = st.text_input(
                    "Precinct suffix",
                    value=suggested_suffix,
                    key="add_precinct_suffix",
                    help="2–4 letters or numbers appended to the district ID.",
                )
                new_precinct_name = st.text_input(
                    "Precinct name",
                    key="add_precinct_name",
                    placeholder=f"Precinct {precinct_suffix or suggested_suffix}",
                )
                try:
                    preview_id = make_precinct_id(
                        add_precinct_district,
                        precinct_suffix or suggested_suffix,
                    )
                    st.caption(f"Precinct ID: **{preview_id}**")
                except PrecinctStoreError as exc:
                    st.caption(str(exc))
                if st.button("Add precinct", use_container_width=True, key="add_precinct_btn"):
                    try:
                        precinct = add_precinct(
                            add_precinct_district,
                            precinct_suffix,
                            new_precinct_name,
                        )
                        init_precinct_data(precinct.id)
                        if st.session_state.mode == "Transmitter":
                            _save_context_settings(
                                active_precinct_id=precinct.id,
                                active_district_id=precinct.district_id,
                            )
                            st.session_state.pending_edits.clear()
                        _configure_receiver()
                        st.toast(f"Added precinct {precinct.id}")
                        st.rerun()
                    except PrecinctStoreError as exc:
                        st.error(str(exc))

        precincts = list_precincts()
        with st.expander("Remove precinct", expanded=False):
            if len(precincts) <= 1:
                st.caption("At least one precinct is required.")
            else:
                precinct_options = {p.id: f"{p.id} — {p.name}" for p in precincts}
                remove_precinct_id = st.selectbox(
                    "Precinct to remove",
                    list(precinct_options),
                    format_func=lambda pid: precinct_options[pid],
                    key="remove_precinct_id",
                )
                st.warning(
                    f"Permanently removes **{remove_precinct_id}** and deletes its local CSV files."
                )
                if st.button("Remove precinct", use_container_width=True, key="remove_precinct_btn"):
                    try:
                        remove_precinct(remove_precinct_id)
                        _switch_context_after_precinct_removed(remove_precinct_id)
                        st.toast(f"Removed {remove_precinct_id}")
                        st.rerun()
                    except PrecinctStoreError as exc:
                        st.error(str(exc))

        with st.expander("Remove district", expanded=False):
            if len(districts) <= 1:
                st.caption("At least one district is required.")
            else:
                district_options = {d.id: f"{d.id} — {d.name}" for d in districts}
                remove_district_id = st.selectbox(
                    "District to remove",
                    list(district_options),
                    format_func=lambda did: district_options[did],
                    key="remove_district_id",
                )
                child_precincts = list_precincts(remove_district_id)
                if child_precincts:
                    st.warning(
                        f"Remove all precincts in **{remove_district_id}** first: "
                        + ", ".join(p.id for p in child_precincts)
                    )
                else:
                    st.warning(
                        f"Permanently removes district **{remove_district_id}**."
                    )
                if st.button(
                    "Remove district",
                    use_container_width=True,
                    key="remove_district_btn",
                    disabled=bool(child_precincts),
                ):
                    try:
                        remove_district(remove_district_id)
                        _switch_context_after_district_removed(remove_district_id)
                        st.toast(f"Removed district {remove_district_id}")
                        st.rerun()
                    except PrecinctStoreError as exc:
                        st.error(str(exc))

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
                    _reconnect_radio(new_settings)
                    st.toast("Settings saved — reconnecting radio")
                    st.rerun()
                except SettingsError as exc:
                    st.error(str(exc))

        if st.button("Reconnect radio", use_container_width=True):
            _reconnect_radio()
            st.toast("Reconnecting radio")
            st.rerun()

        st.divider()
        show_mock_testing = st.checkbox(
            "Show mock testing tools",
            value=bool(st.session_state.app_settings.get("show_mock_testing", True)),
            help="Hide the packet injection panel when you do not need it.",
            key="show_mock_testing_toggle",
        )
        if show_mock_testing != bool(st.session_state.app_settings.get("show_mock_testing", True)):
            _save_context_settings(show_mock_testing=show_mock_testing)
            st.rerun()

        if show_mock_testing:
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
                st.rerun()

        _render_text_messages()

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

        with st.expander("Import addresses", expanded=False):
            st.caption(
                "Upload or paste a CSV with `house_id,address` columns. "
                "Only houses on this precinct's board are updated."
            )
            uploaded = st.file_uploader(
                "Address CSV",
                type=["csv"],
                key="import_address_file",
            )
            pasted = st.text_area(
                "Or paste CSV",
                placeholder="house_id,address\nH001,142 Oak St\nH002,144 Oak St",
                key="import_address_text",
                height=120,
            )

            csv_text = ""
            if uploaded is not None:
                csv_text = uploaded.getvalue().decode("utf-8-sig")
            elif pasted.strip():
                csv_text = pasted

            preview_rows: list[dict[str, str]] = []
            parse_error = ""
            if csv_text.strip():
                try:
                    preview_rows = parse_address_csv(csv_text)
                except AddressStoreError as exc:
                    parse_error = str(exc)

            if parse_error:
                st.error(parse_error)
            elif preview_rows:
                known_ids = {house_id.upper() for house_id in house_ids}
                importable = sum(1 for row in preview_rows if row["house_id"] in known_ids)
                skipped = len(preview_rows) - importable
                st.caption(
                    f"**{len(preview_rows)}** rows parsed — "
                    f"**{importable}** will import, **{skipped}** skipped (unknown house IDs)."
                )
                st.code(
                    "\n".join(
                        f"{row['house_id']},{row['address']}"
                        for row in preview_rows[:5]
                    )
                    + ("\n..." if len(preview_rows) > 5 else ""),
                    language="text",
                )

            if st.button("Import addresses", use_container_width=True, key="import_address_btn"):
                if not house_ids:
                    st.error("Add houses to this precinct before importing addresses.")
                elif not csv_text.strip():
                    st.error("Upload a CSV file or paste address rows first.")
                elif parse_error:
                    st.error(parse_error)
                else:
                    try:
                        rows = parse_address_csv(csv_text)
                        result = import_addresses(
                            rows,
                            path=paths.addresses,
                            known_house_ids={house_id.upper() for house_id in house_ids},
                        )
                        st.toast(
                            "Imported addresses: "
                            f"{result['added']} added, "
                            f"{result['updated']} updated, "
                            f"{result['skipped']} skipped"
                        )
                        st.rerun()
                    except AddressStoreError as exc:
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
    _render_print_board_actions(
        rows,
        title="Neighborhood Status Board",
        subtitle=f"Precinct: {precinct_label}",
        file_stem=_active_precinct_id(),
        paths=paths,
    )
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


def _render_text_message_alert() -> None:
    alert = st.session_state.get("text_message_alert")
    if not alert:
        return

    sender = alert.get("sender_name") or alert.get("from_id") or "Unknown"
    body = format_message_text(sender, alert["text"])
    time_label = _format_change_time(alert["at"])

    st.markdown(
        f"""
        <div class="text-message-alert">
            <strong>📩 New text message · {html.escape(time_label)}</strong><br>
            {html.escape(body)}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.button(
        "Dismiss alert",
        key="dismiss_text_alert",
        use_container_width=True,
        on_click=_dismiss_text_alert,
    )


def _render_text_message_alert_section() -> None:
    _sync_text_messages()
    _render_text_message_alert()


@st.fragment(run_every=2)
def _render_text_message_alert_section_live() -> None:
    _render_text_message_alert_section()


@st.fragment(run_every=2)
def _render_text_messages() -> None:
    channel = st.session_state.app_settings.get("channel_name", "charcStatus")
    with st.expander("Text messages", expanded=False):
        st.caption(
            f"Send free-form text on mesh channel **{channel}**. "
            "Status packets (`NS:...`) are handled separately."
        )
        message = st.text_area(
            "Message",
            key="mesh_text_message",
            height=100,
            placeholder="Need extra water at staging point.",
        )
        byte_count = len(message.encode("utf-8"))
        st.caption(f"{byte_count} / {MESH_MAX_PAYLOAD_BYTES} bytes")

        if st.button("Send message", use_container_width=True, key="send_text_message_btn"):
            try:
                cleaned = validate_message(message)
                ok, detail = st.session_state.client.send_text(cleaned)
                if ok:
                    record_sent(cleaned, from_id=st.session_state.client.local_node_id())
                    st.toast("Message sent")
                    st.rerun()
                else:
                    st.error(detail)
            except TextMessageError as exc:
                st.error(str(exc))

        messages = _sync_text_messages()
        if messages:
            st.markdown("**Recent messages**")
            for item in messages[:12]:
                prefix = "Sent" if item["direction"] == "sent" else "Received"
                sender = item.get("sender_name") or item.get("from_id")
                body = format_message_text(sender, item["text"])
                st.markdown(
                    f"**{prefix} · {_format_change_time(item['at'])}**  \n{body}"
                )
        else:
            st.caption("No text messages yet.")


def _render_receiver_mode() -> None:
    district_id = _active_district_id()
    districts = {d.id: d.name for d in list_districts()}
    district_label = f"{district_id} — {districts.get(district_id, district_id)}"

    st.title("📥 Receiver Mode")
    st.caption(f"District: **{district_label}**")
    st.caption("Listening for mesh updates and refreshing the status board.")

    receiver: MeshReceiver = st.session_state.receiver
    _restart_receiver_if_needed()

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
    _render_print_board_actions(
        rows,
        title="District Status Board",
        subtitle=f"District: {district_label}",
        file_stem=district_id,
        show_precinct=True,
        change_labels=change_labels,
    )
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
    _ensure_text_message_listener()
    _render_connection_banner()
    if st.session_state.mode == "Transmitter":
        _render_text_message_alert_section_live()
    else:
        _render_text_message_alert_section()
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
