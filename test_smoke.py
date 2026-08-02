"""Quick smoke tests for packet codec and offline fallback."""

from packet_codec import (
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


def test_addresses_local_only() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "addresses.csv"
        update_address("H001", "142 Oak St", path=path)
        rows = [{"house_id": "H001", "status_code": "RED", "timestamp": "t"}]
        enriched = attach_addresses(rows, path=path)
        assert enriched[0]["address"] == "142 Oak St"

        from packet_codec import encode_status

        assert encode_status("H001", "RED") == "NS:H001:R"
        assert "Oak" not in encode_status("H001", "RED")

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
    pkt = encode_status("H001", "RED")
    assert pkt == "NS:H001:R"
    assert decode_packet(pkt) == ("H001", "RED")
    assert decode_packet("hello mesh") is None

    bulk = encode_bulk_sync([("H001", "YELLOW"), ("H002", "RED"), ("H003", "GREEN")])
    assert bulk == "NS:B:H001Y,H002R,H003G"
    assert decode_updates(bulk) == [
        ("H001", "YELLOW"),
        ("H002", "RED"),
        ("H003", "GREEN"),
    ]
    assert decode_updates("NS:H004:G") == [("H004", "GREEN")]

    # Force chunking with a small byte limit
    many = [(f"H{i:03d}", "GREEN") for i in range(1, 21)]
    chunks = encode_bulk_sync_chunks(many, max_bytes=60)
    assert len(chunks) > 1
    merged: list[tuple[str, str]] = []
    for chunk in chunks:
        merged.extend(decode_updates(chunk))
    assert merged == many
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
        assert defaults["sync_packet_delay"] == 2.0

        saved = settings_store.save_settings(
            {
                "meshtastic_port": "/dev/ttyUSB0",
                "channel_name": "blockA",
                "sync_packet_delay": 3.5,
            }
        )
        assert saved["meshtastic_port"] == "/dev/ttyUSB0"
        assert saved["channel_name"] == "blockA"
        assert saved["sync_packet_delay"] == 3.5

        reloaded = settings_store.load_settings()
        assert reloaded == saved

        try:
            settings_store.save_settings({"channel_name": "", "sync_packet_delay": 2.0})
            assert False, "expected SettingsError"
        except settings_store.SettingsError:
            pass
    finally:
        config.SETTINGS_PATH = orig

    print("settings_store: OK")
def test_house_management(tmp_path) -> None:
    import csv
    from pathlib import Path

    import address_store
    import config
    import csv_store
    import house_store
    import sync_state

    status_path = tmp_path / "status.csv"
    addr_path = tmp_path / "addresses.csv"
    sync_path = tmp_path / "last_sync.csv"

    orig = {
        "config_csv": config.CSV_PATH,
        "config_addr": config.ADDRESSES_PATH,
        "config_sync": config.LAST_SYNC_PATH,
        "csv": csv_store.CSV_PATH,
        "addr": address_store.ADDRESSES_PATH,
        "sync": sync_state.LAST_SYNC_PATH,
    }
    config.CSV_PATH = status_path
    config.ADDRESSES_PATH = addr_path
    config.LAST_SYNC_PATH = sync_path
    csv_store.CSV_PATH = status_path
    address_store.ADDRESSES_PATH = addr_path
    sync_state.LAST_SYNC_PATH = sync_path

    try:
        from address_store import read_address_map
        from csv_store import read_all, update_status
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

        row = house_store.add_house("H003", "303 Pine St")
        assert row["house_id"] == "H003"
        assert row["status_code"] == "GREEN"
        assert read_address_map(path=addr_path)["H003"] == "303 Pine St"
        assert house_store.suggest_next_house_id() == "H004"

        save_last_sync(read_all(path=status_path), path=sync_path)

        house_store.rename_house("H003", "H030")
        ids = [r["house_id"] for r in read_all(path=status_path)]
        assert "H030" in ids
        assert "H003" not in ids
        assert read_address_map(path=addr_path)["H030"] == "303 Pine St"
        assert read_last_sync(path=sync_path)["H030"] == "GREEN"
        assert "H003" not in read_last_sync(path=sync_path)

        house_store.remove_house("H030")
        ids = [r["house_id"] for r in read_all(path=status_path)]
        assert "H030" not in ids
        assert "H030" not in read_address_map(path=addr_path)
        assert "H030" not in read_last_sync(path=sync_path)

        # Simulate app startup — must not resurrect a deleted house.
        from address_store import init_addresses as addr_init
        from csv_store import init_csv as status_init

        house_store.remove_house("H002")
        status_init(status_path)
        addr_init(addr_path)
        ids = [r["house_id"] for r in read_all(path=status_path)]
        assert "H002" not in ids
        assert "H002" not in read_address_map(path=addr_path)
    finally:
        config.CSV_PATH = orig["config_csv"]
        config.ADDRESSES_PATH = orig["config_addr"]
        config.LAST_SYNC_PATH = orig["config_sync"]
        csv_store.CSV_PATH = orig["csv"]
        address_store.ADDRESSES_PATH = orig["addr"]
        sync_state.LAST_SYNC_PATH = orig["sync"]

    print("house_management: OK")


def test_mock_fallback() -> None:
    client = MeshtasticClient()
    info = client.connect()
    if info.mock_mode:
        ok, msg = client.send_text("NS:H002:Y")
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
    test_addresses_local_only()
    test_urgency_sort()
    with tempfile.TemporaryDirectory() as tmp:
        test_settings_store(Path(tmp))
        test_house_management(Path(tmp))
    test_mock_fallback()
    print("All smoke tests passed.")
