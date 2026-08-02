"""Quick smoke tests for packet codec and offline fallback."""

from packet_codec import (
    decode_packet,
    decode_updates,
    encode_bulk_sync,
    encode_bulk_sync_chunks,
    encode_status,
)
from meshtastic_client import MeshtasticClient


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
    test_packet_codec()
    test_mock_fallback()
    print("All smoke tests passed.")
