"""Neighborhood Block Captain — Meshtastic emergency status tool."""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd
import streamlit as st

from config import (
    STATUS_BG,
    STATUS_CODES,
    STATUS_COLORS,
    STATUS_LABELS,
)
from csv_store import ensure_default_houses, init_csv, read_all, update_status
from meshtastic_client import MeshtasticClient, list_serial_ports
from packet_codec import encode_bulk_sync_chunks
from receiver import MeshReceiver
from sync_state import (
    compute_changes_since_baseline,
    compute_sync_rows,
    has_last_sync,
    rows_to_baseline,
    save_last_sync,
)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    added = ensure_default_houses()
    if added:
        logger.info("Added %s default houses to CSV", added)
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
        st.caption(f"Mesh channel: **{info.channel_name}**")
        if info.channel_index is not None:
            st.caption(f"Channel index: {info.channel_index}")
        ports = list_serial_ports()
        if ports:
            st.caption("Detected serial ports:")
            for p in ports:
                st.code(p)
        else:
            st.caption("No serial ports detected.")

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

        if st.button("Reconnect radio", use_container_width=True):
            st.session_state.client.close()
            st.session_state.client = MeshtasticClient()
            st.session_state.receiver = MeshReceiver(st.session_state.client)
            st.rerun()

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


def _render_status_table(rows: list[dict]) -> None:
    st.subheader("Neighborhood Status Board")

    header = st.columns([2, 3, 3, 2])
    header[0].markdown("**House**")
    header[1].markdown("**Current Status**")
    header[2].markdown("**Change Status**")
    header[3].markdown("**Last Updated**")

    for row in rows:
        house_id = row["house_id"]
        current = row["status_code"]
        ts = row.get("timestamp", "")

        cols = st.columns([2, 3, 3, 2])
        cols[0].markdown(f"### {house_id}")
        cols[1].markdown(_status_pill(current), unsafe_allow_html=True)

        selected = cols[2].selectbox(
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
        cols[3].caption(display_ts)


def _apply_pending_edits() -> None:
    for house_id, status in st.session_state.pending_edits.items():
        update_status(house_id, status)
    if st.session_state.pending_edits:
        st.session_state.pending_edits.clear()


def _render_transmitter_mode() -> None:
    st.title("📤 Transmitter Mode")
    st.caption("Update house statuses and sync to the mesh network.")
    if has_last_sync():
        st.caption("Only changed houses are sent after the first successful sync.")
    else:
        st.caption("First sync transmits the full neighborhood board.")

    rows = read_all()
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

        success, errors = st.session_state.client.send_many(packets)
        mode_label = "Full sync" if sync_mode == "full" else "Delta sync"
        st.session_state.last_sync_log = [
            f"{mode_label}: {len(sync_rows)} house(s) in {len(packets)} packet(s)",
            *[f"  [{i + 1}] {pkt}" for i, pkt in enumerate(packets)],
        ] + errors

        if errors:
            st.error(f"Sent {success}/{len(packets)} packets — some failed.")
        elif len(packets) == 1:
            st.success(
                f"{mode_label}: sent {len(sync_rows)} house status(es) in one mesh packet!"
            )
        else:
            st.success(
                f"{mode_label}: sent {len(sync_rows)} house status(es) "
                f"across {len(packets)} mesh packets!"
            )

        if success == len(packets):
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
    rows = read_all()
    baseline = st.session_state.receiver_baseline
    pending = compute_changes_since_baseline(rows, baseline)

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

    if pending:
        st.subheader(f"Changes since watch started ({len(pending)})")
        st.caption(
            "Houses that differ from when you opened Receiver mode or last marked reviewed."
        )
        change_rows = [
            {
                "House": c.house_id,
                "Was": c.previous_status or "—",
                "Now": c.current_status,
                "Updated": _format_change_time(c.timestamp),
            }
            for c in pending
        ]
        st.dataframe(
            pd.DataFrame(change_rows),
            use_container_width=True,
            hide_index=True,
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
    st.subheader("Full neighborhood board")

    show_changed_only = st.checkbox(
        "Show changed houses only",
        value=False,
        disabled=len(pending) == 0,
    )

    display_rows = rows
    if show_changed_only and pending:
        pending_ids = {c.house_id for c in pending}
        display_rows = [r for r in rows if r["house_id"].upper() in pending_ids]

    df = pd.DataFrame(display_rows)
    if not df.empty:
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "house_id": st.column_config.TextColumn("House", width="small"),
                "status_code": st.column_config.TextColumn("Status", width="medium"),
                "timestamp": st.column_config.TextColumn("Updated", width="medium"),
            },
        )
    else:
        st.caption("No rows to display.")

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
