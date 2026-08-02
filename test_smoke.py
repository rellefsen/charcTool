"""Quick smoke tests for packet codec and offline fallback."""

from packet_codec import (
    decode_packet,
    decode_updates,
    encode_bulk_sync,
    encode_bulk_sync_chunks,
    encode_status,
)
from meshtastic_client import MeshtasticClient
from sync_state import (
    compute_changes_since_baseline,
    compute_sync_rows,
    rows_to_baseline,
    save_last_sync,
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
    test_mock_fallback()
    print("All smoke tests passed.")
