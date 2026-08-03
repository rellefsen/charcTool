"""Quick smoke tests for packet codec and offline fallback."""

from packet_codec import (
    MeshUpdate,
    decode_packet,
    decode_updates,
    encode_bulk_sync,
    encode_bulk_sync_chunks,
    encode_status,
)
from address_store import attach_addresses, default_address, update_address
from csv_store import sort_rows_by_urgency
from meshtastic_client import MeshtasticClient
from sync_state import (
    compute_changes_since_baseline,
    compute_sync_rows,
    format_status_change,
    rows_to_baseline,
    save_last_sync,
    sort_changes_by_urgency,
)


def test_sync_state_delta(tmp_path) -> None:
    csv_path = tmp_path / "status.csv"
    sync_path = tmp_path / "last_sync.csv"

    rows = [
        {"house_id": "H001", "status_code": "GREEN", "timestamp": "t"},
        {"house_id": "H002", "status_code": "RED", "timestamp": "t"},
    ]

    to_send, mode = compute_sync_rows(rows, last_sync_path=sync_path)
    assert mode == "full"
    assert len(to_send) == 2

    save_last_sync(rows, path=sync_path)
    to_send, mode = compute_sync_rows(rows, last_sync_path=sync_path)
    assert mode == "none"
    assert to_send == []

    rows[0]["status_code"] = "YELLOW"
    to_send, mode = compute_sync_rows(rows, last_sync_path=sync_path)
    assert mode == "delta"
    assert to_send == [("H001", "YELLOW")]
    print("sync_state: OK")


def test_receiver_changes() -> None:
    rows = [
        {"house_id": "H001", "status_code": "GREEN", "timestamp": "t1"},
        {"house_id": "H002", "status_code": "RED", "timestamp": "t2"},
    ]
    baseline = rows_to_baseline(rows)
    rows[0]["status_code"] = "YELLOW"
    rows[1]["status_code"] = "RED"
    changes = compute_changes_since_baseline(rows, baseline)
    assert len(changes) == 1
    assert changes[0].house_id == "H001"
    assert changes[0].previous_status == "GREEN"
    assert changes[0].current_status == "YELLOW"
    print("receiver_changes: OK")


def test_status_change_label() -> None:
    assert format_status_change("GREEN", "RED") == "GREEN → RED"
    assert format_status_change("YELLOW", "GREEN") == "YELLOW → GREEN"
    assert format_status_change(None, "RED") == "NEW → RED"
    print("status_change_label: OK")


def test_addresses_local_only() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "addresses.csv"
        update_address("H001", "142 Oak St", path=path)
        rows = [{"house_id": "H001", "status_code": "RED", "timestamp": "t"}]
        enriched = attach_addresses(rows, path=path)
        assert enriched[0]["address"] == "142 Oak St"

        assert encode_status("CHARC01", "H001", "RED") == "NS:CHARC01:H001:R"
        assert "Oak" not in encode_status("CHARC01", "H001", "RED")

    assert default_address("H012") == "12 Oak St"
    print("addresses: OK")


def test_urgency_sort() -> None:
    rows = [
        {"house_id": "H003", "status_code": "GREEN", "timestamp": "t"},
        {"house_id": "H001", "status_code": "RED", "timestamp": "t"},
        {"house_id": "H002", "status_code": "YELLOW", "timestamp": "t"},
    ]
    assert [r["house_id"] for r in sort_rows_by_urgency(rows)] == ["H001", "H002", "H003"]

    from sync_state import HouseChange

    changes = sort_changes_by_urgency(
        [
            HouseChange("H010", "GREEN", "YELLOW", "t"),
            HouseChange("H005", "GREEN", "RED", "t"),
        ]
    )
    assert [c.house_id for c in changes] == ["H005", "H010"]
    print("urgency_sort: OK")


def test_packet_codec() -> None:
    pkt = encode_status("CHARC01", "H001", "RED")
    assert pkt == "NS:CHARC01:H001:R"
    decoded = decode_packet(pkt)
    assert decoded == MeshUpdate(precinct_id="CHARC01", house_id="H001", status_code="RED")
    assert decode_packet("hello mesh") is None

    bulk = encode_bulk_sync(
        "CHARC01",
        [("H001", "YELLOW"), ("H002", "RED"), ("H003", "GREEN")],
    )
    assert bulk == "NS:CHARC01:B:H001Y,H002R,H003G"
    assert decode_updates(bulk) == [
        MeshUpdate(precinct_id="CHARC01", house_id="H001", status_code="YELLOW"),
        MeshUpdate(precinct_id="CHARC01", house_id="H002", status_code="RED"),
        MeshUpdate(precinct_id="CHARC01", house_id="H003", status_code="GREEN"),
    ]
    assert decode_updates("NS:CHARC02:H004:G") == [
        MeshUpdate(precinct_id="CHARC02", house_id="H004", status_code="GREEN"),
    ]

    # Legacy packets still decode (no precinct tag)
    assert decode_updates("NS:H004:G") == [
        MeshUpdate(precinct_id=None, house_id="H004", status_code="GREEN"),
    ]
    assert decode_updates("NS:B:H001Y,H002R") == [
        MeshUpdate(precinct_id=None, house_id="H001", status_code="YELLOW"),
        MeshUpdate(precinct_id=None, house_id="H002", status_code="RED"),
    ]

    many = [(f"H{i:03d}", "GREEN") for i in range(1, 21)]
    chunks = encode_bulk_sync_chunks("CHARC01", many, max_bytes=70)
    assert len(chunks) > 1
    merged: list[MeshUpdate] = []
    for chunk in chunks:
        merged.extend(decode_updates(chunk))
    assert [(u.house_id, u.status_code) for u in merged] == many
    print("packet_codec: OK")


def test_settings_store(tmp_path) -> None:
    import config
    import settings_store

    settings_path = tmp_path / "app_settings.json"
    orig = config.SETTINGS_PATH
    config.SETTINGS_PATH = settings_path

    try:
        defaults = settings_store.load_settings()
        assert defaults["channel_name"] == "charcStatus"
        assert defaults["active_precinct_id"] == "CHARC01"
        assert defaults["active_district_id"] == "CHARC"
        assert defaults["show_mock_testing"] is True

        saved = settings_store.save_settings(
            {
                "meshtastic_port": "/dev/ttyUSB0",
                "channel_name": "blockA",
                "sync_packet_delay": 3.5,
                "active_precinct_id": "CHARC02",
                "active_district_id": "CHARC",
                "show_mock_testing": False,
            }
        )
        assert saved["active_precinct_id"] == "CHARC02"
        assert saved["show_mock_testing"] is False
        reloaded = settings_store.load_settings()
        assert reloaded == saved
    finally:
        config.SETTINGS_PATH = orig

    print("settings_store: OK")


def test_precinct_store(tmp_path) -> None:
    import config
    import precinct_store

    orig_org = config.ORGANIZATION_PATH
    orig_precincts = config.PRECINCTS_DIR
    config.ORGANIZATION_PATH = tmp_path / "organization.json"
    config.PRECINCTS_DIR = tmp_path / "precincts"

    try:
        org = precinct_store.init_organization()
        assert org["districts"][0]["id"] == "CHARC"
        assert org["precincts"][0]["id"] == "CHARC01"

        district = precinct_store.add_district("SOUTH", "South District")
        assert district.id == "SOUTH"
        assert {d.id for d in precinct_store.list_districts()} == {"CHARC", "SOUTH"}

        precinct = precinct_store.add_precinct("CHARC", "02", "Pine Ridge")
        assert precinct.id == "CHARC02"
        assert precinct_store.make_precinct_id("CHARC", "03") == "CHARC03"
        assert precinct_store.suggest_next_precinct_suffix("CHARC") == "03"
        assert precinct_store.precinct_ids_for_district("CHARC") == {"CHARC01", "CHARC02"}

        south_precinct = precinct_store.add_precinct("SOUTH", "01", "South 01")
        assert south_precinct.id == "SOUTH01"
        precinct_store.remove_precinct("SOUTH01")
        assert south_precinct.id not in precinct_store.precinct_ids_for_district("SOUTH")
        assert not (config.PRECINCTS_DIR / "SOUTH01").exists()

        precinct_store.remove_precinct("CHARC02")
        assert precinct_store.precinct_ids_for_district("CHARC") == {"CHARC01"}
        assert not (config.PRECINCTS_DIR / "CHARC02").exists()

        precinct_store.remove_district("SOUTH")
        assert {d.id for d in precinct_store.list_districts()} == {"CHARC"}

        paths = precinct_store.paths_for_precinct("CHARC01")
        assert paths.status.name == "neighborhood_status.csv"
    finally:
        config.ORGANIZATION_PATH = orig_org
        config.PRECINCTS_DIR = orig_precincts

    print("precinct_store: OK")


def test_house_management(tmp_path) -> None:
    import csv

    import address_store
    import config
    import csv_store
    import house_store
    import precinct_store
    import sync_state

    status_path = tmp_path / "status.csv"
    addr_path = tmp_path / "addresses.csv"
    sync_path = tmp_path / "last_sync.csv"
    paths = precinct_store.PrecinctPaths(
        precinct_id="CHARC01",
        status=status_path,
        addresses=addr_path,
        last_sync=sync_path,
    )

    try:
        from address_store import read_address_map
        from csv_store import read_all
        from sync_state import read_last_sync, save_last_sync

        with status_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=config.CSV_FIELDS)
            writer.writeheader()
            writer.writerow(
                {"house_id": "H001", "status_code": "RED", "timestamp": "t1"}
            )
            writer.writerow(
                {"house_id": "H002", "status_code": "GREEN", "timestamp": "t2"}
            )

        with addr_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=config.ADDRESS_FIELDS)
            writer.writeheader()
            writer.writerow({"house_id": "H001", "address": "101 Oak St"})
            writer.writerow({"house_id": "H002", "address": "102 Oak St"})

        save_last_sync(read_all(path=status_path), path=sync_path)

        row = house_store.add_house("H003", paths, "303 Pine St")
        assert row["house_id"] == "H003"
        assert house_store.suggest_next_house_id(paths) == "H004"

        save_last_sync(read_all(path=status_path), path=sync_path)

        house_store.rename_house("H003", "H030", paths)
        assert "H030" in [r["house_id"] for r in read_all(path=status_path)]
        assert read_last_sync(path=sync_path)["H030"] == "GREEN"

        house_store.remove_house("H030", paths)
        assert "H030" not in [r["house_id"] for r in read_all(path=status_path)]
    finally:
        pass

    print("house_management: OK")


def test_bulk_address_import(tmp_path) -> None:
    import csv

    from address_store import import_addresses, parse_address_csv, read_address_map
    from config import ADDRESS_FIELDS, CSV_FIELDS
    from packet_codec import encode_status
    from precinct_store import PrecinctPaths

    status_path = tmp_path / "status.csv"
    addr_path = tmp_path / "addresses.csv"
    paths = PrecinctPaths(
        precinct_id="CHARC01",
        status=status_path,
        addresses=addr_path,
        last_sync=tmp_path / "last_sync.csv",
    )

    with status_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow({"house_id": "H001", "status_code": "GREEN", "timestamp": "t1"})
        writer.writerow({"house_id": "H002", "status_code": "GREEN", "timestamp": "t1"})

    with addr_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=ADDRESS_FIELDS)
        writer.writeheader()
        writer.writerow({"house_id": "H001", "address": "101 Oak St"})
        writer.writerow({"house_id": "H002", "address": "102 Oak St"})

    csv_text = "house_id,address\nH001,142 Oak St\nH002,144 Oak St\nH999,999 Unknown St"
    rows = parse_address_csv(csv_text)
    result = import_addresses(
        rows,
        path=paths.addresses,
        known_house_ids={"H001", "H002"},
    )
    assert result == {"updated": 2, "added": 0, "skipped": 1}

    address_map = read_address_map(path=paths.addresses)
    assert address_map["H001"] == "142 Oak St"
    assert address_map["H002"] == "144 Oak St"
    assert "H999" not in address_map
    assert "Oak" not in encode_status("CHARC01", "H001", "GREEN")

    print("bulk_address_import: OK")


def test_printable_board_html() -> None:
    from print_board import build_printable_html, status_counts

    rows = [
        {
            "house_id": "H001",
            "status_code": "RED",
            "timestamp": "2026-08-02T01:30:06Z",
            "address": "142 Oak St",
        },
        {
            "house_id": "H002",
            "status_code": "GREEN",
            "timestamp": "2026-08-02T01:30:06Z",
            "address": "144 Oak St",
        },
    ]
    html = build_printable_html(
        rows,
        title="Neighborhood Status Board",
        subtitle="Precinct: CHARC01 — North Precinct 01",
    )
    assert "H001" in html
    assert "142 Oak St" in html
    assert "RED" in html
    assert "<table" in html
    assert status_counts(rows)["RED"] == 1

    district_rows = [
        {
            **rows[0],
            "precinct_id": "CHARC01",
        }
    ]
    district_html = build_printable_html(
        district_rows,
        title="District Status Board",
        subtitle="District: CHARC — North District",
        show_precinct=True,
        change_labels={"CHARC01:H001": "GREEN → RED"},
    )
    assert "CHARC01" in district_html
    assert "GREEN → RED" in district_html
    assert "Was → Now" in district_html

    print("printable_board: OK")


def test_text_messages() -> None:
    from packet_codec import is_status_packet
    from text_messages import (
        TextMessageError,
        clear_messages,
        drain_pending,
        format_message_text,
        record_received,
        record_sent,
        validate_message,
    )

    clear_messages()
    assert is_status_packet("NS:CHARC01:H001:R") is True
    assert is_status_packet("Need help at staging") is False

    assert validate_message("  hello mesh  ") == "hello mesh"
    try:
        validate_message("   ")
        assert False, "expected empty message error"
    except TextMessageError:
        pass

    record_sent("Staging needs more water", from_id="!LOCAL01")
    record_received("NS:CHARC01:H001:R")
    received = record_received("Copy that", from_id="!RADIO42")
    assert received is not None
    assert received.text == "Copy that"
    assert received.from_id == "!RADIO42"

    pending = drain_pending()
    assert pending[0].text == "Copy that"
    assert pending[0].from_id == "!RADIO42"
    assert pending[1].from_id == "!LOCAL01"
    assert format_message_text("North Relay", "Copy that") == "North Relay: Copy that"
    assert drain_pending() == []

    print("text_messages: OK")


def test_node_display_name() -> None:
    client = MeshtasticClient()
    assert client.node_display_name("LOCAL") == "Local Radio"
    assert client.node_display_name("!MOCK") == "Mock Radio"

    client._mock_mode = False
    client._interface = type(
        "Iface",
        (),
        {
            "nodesByNum": {
                0x28B5465C: {
                    "user": {
                        "id": "!28b5465c",
                        "longName": "North Relay",
                        "shortName": "NR01",
                    }
                }
            },
            "getMyUser": lambda self: None,
            "getLongName": lambda self: None,
            "getShortName": lambda self: None,
            "myInfo": None,
        },
    )()
    assert client.node_display_name("!28b5465c") == "North Relay"
    assert client.node_display_name("!deadbeef") == "!deadbeef"

    print("node_display_name: OK")


def test_text_message_dispatch() -> None:
    from text_messages import clear_messages, drain_pending, record_received

    clear_messages()
    client = MeshtasticClient()
    client.register_receive_callback(
        lambda text, from_id=None: record_received(text, from_id=from_id)
    )
    client.dispatch_message("Meet at the church steps", "!MOCK01")
    client.dispatch_message("NS:CHARC01:H001:R", "!MOCK01")

    pending = drain_pending()
    assert len(pending) == 1
    assert pending[0].text == "Meet at the church steps"
    assert pending[0].from_id == "!MOCK01"

    clear_messages()
    client._mock_mode = True
    client.send_text("Mock reply")
    pending = drain_pending()
    assert len(pending) == 1
    assert pending[0].text == "Mock reply"
    assert pending[0].from_id == "LOCAL"

    print("text_message_dispatch: OK")


def test_global_pubsub_routing() -> None:
    from pubsub import pub

    from meshtastic_client import MeshtasticClient, _ensure_global_pubsub, _set_active_client
    from text_messages import clear_messages, drain_pending, record_received

    clear_messages()
    _ensure_global_pubsub()
    client = MeshtasticClient()
    client._channel_index = 0
    client.register_receive_callback(
        lambda text, from_id=None: record_received(text, from_id=from_id)
    )
    _set_active_client(client)

    pub.sendMessage(
        "meshtastic.receive.text",
        packet={
            "channel": 0,
            "fromId": "!abc123",
            "decoded": {"text": "Hello mesh"},
        },
    )

    pending = drain_pending()
    assert len(pending) == 1
    assert pending[0].text == "Hello mesh"
    assert pending[0].from_id == "!abc123"

    clear_messages()
    pub.sendMessage(
        "meshtastic.receive.text",
        packet={
            "channel": 1,
            "fromId": "!abc123",
            "decoded": {"text": "Wrong channel"},
        },
    )
    assert len(drain_pending()) == 0

    client2 = MeshtasticClient()
    client2._channel_index = 0
    client2.register_receive_callback(
        lambda text, from_id=None: record_received(text, from_id=from_id)
    )
    _set_active_client(client2)
    pub.sendMessage(
        "meshtastic.receive.text",
        packet={"channel": 0, "decoded": {"text": "After reconnect"}},
    )
    assert drain_pending()[0].text == "After reconnect"

    print("global_pubsub_routing: OK")


def test_mesh_data_export(tmp_path) -> None:
    import config
    import address_store
    import csv_store
    import precinct_store
    from mesh_data_codec import (
        MeshDataKind,
        build_full_export_packets,
        decode_mesh_data,
        encode_address_chunks,
        encode_district,
        encode_export_end,
        encode_export_start,
        encode_precinct,
    )
    from receiver import MeshReceiver
    from text_messages import clear_messages, drain_pending, record_received

    orig_org = config.ORGANIZATION_PATH
    orig_precincts = config.PRECINCTS_DIR
    config.ORGANIZATION_PATH = tmp_path / "tx" / "organization.json"
    config.PRECINCTS_DIR = tmp_path / "tx" / "precincts"

    try:
        precinct_store.init_organization()
        precinct_store.add_district("SOUTH", "South District")
        precinct_store.add_precinct("SOUTH", "01", "South Precinct 01")
        paths = precinct_store.paths_for_precinct("SOUTH01")
        csv_store.init_csv(paths.status)
        address_store.init_addresses(paths.addresses)
        address_store.update_address("H001", "42 Pine St", path=paths.addresses)
        csv_store.update_status("H001", "RED", path=paths.status)

        assert decode_mesh_data(encode_export_start()).kind == MeshDataKind.START
        assert decode_mesh_data(encode_export_end()).kind == MeshDataKind.END
        district_packet = encode_district("SOUTH", "South District")
        decoded_district = decode_mesh_data(district_packet)
        assert decoded_district is not None
        assert decoded_district.kind == MeshDataKind.DISTRICT
        assert decoded_district.district_id == "SOUTH"

        address_packets = encode_address_chunks(
            "SOUTH01",
            [("H001", "42 Pine St"), ("H002", "99 Elm Avenue")],
            max_bytes=45,
        )
        assert len(address_packets) >= 2
        decoded_addresses = decode_mesh_data(address_packets[0])
        assert decoded_addresses is not None
        assert decoded_addresses.kind == MeshDataKind.ADDRESSES
        assert decoded_addresses.addresses[0].house_id == "H001"

        packets = build_full_export_packets()
        assert packets[0].upper().startswith("ND:S:")
        assert packets[-1].upper().startswith("ND:Z:")
        assert any("ND:D:SOUTH" in p.upper() for p in packets)
        assert any("ND:P:SOUTH01" in p.upper() for p in packets)
        assert any("ND:A:SOUTH01" in p.upper() for p in packets)
        assert any("NS:SOUTH01:B:" in p.upper() for p in packets)

        clear_messages()
        assert record_received(encode_district("SOUTH", "South District")) is None
        assert record_received("Hello operator") is not None
        clear_messages()

        config.ORGANIZATION_PATH = tmp_path / "rx" / "organization.json"
        config.PRECINCTS_DIR = tmp_path / "rx" / "precincts"
        precinct_store.init_organization()

        client = MeshtasticClient()
        receiver = MeshReceiver(client)
        for packet in packets:
            receiver._handle_message(packet)

        assert "SOUTH" in {d.id for d in precinct_store.list_districts()}
        assert "SOUTH01" in precinct_store.precinct_ids_for_district("SOUTH")
        recv_paths = precinct_store.paths_for_precinct("SOUTH01")
        addresses = address_store.read_address_map(recv_paths.addresses)
        assert addresses.get("H001") == "42 Pine St"
        rows = csv_store.read_all(recv_paths.status)
        assert any(r["house_id"] == "H001" and r["status_code"] == "RED" for r in rows)
        assert receiver.stats.import_mode is False
        assert receiver.stats.data_imports_applied > 0
    finally:
        config.ORGANIZATION_PATH = orig_org
        config.PRECINCTS_DIR = orig_precincts

    print("mesh_data_export: OK")


def test_mock_fallback() -> None:
    client = MeshtasticClient()
    info = client.connect()
    if info.mock_mode:
        ok, msg = client.send_text("NS:CHARC01:H002:Y")
        assert ok is True
        assert "Mock transmit" in msg
        print("mock_fallback: OK")
    else:
        assert info.connected is True
        print(f"mock_fallback: skipped (radio connected on {info.port})")
    client.close()


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    test_packet_codec()
    with tempfile.TemporaryDirectory() as tmp:
        test_sync_state_delta(Path(tmp))
    test_receiver_changes()
    test_status_change_label()
    test_addresses_local_only()
    test_urgency_sort()
    with tempfile.TemporaryDirectory() as tmp:
        test_settings_store(Path(tmp))
        test_precinct_store(Path(tmp))
        test_house_management(Path(tmp))
        test_bulk_address_import(Path(tmp))
    test_printable_board_html()
    test_text_messages()
    test_node_display_name()
    test_text_message_dispatch()
    test_global_pubsub_routing()
    with tempfile.TemporaryDirectory() as tmp:
        test_mesh_data_export(Path(tmp))
    test_mock_fallback()
    print("All smoke tests passed.")
