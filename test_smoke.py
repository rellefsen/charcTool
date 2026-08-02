"""Quick smoke tests for packet codec and offline fallback."""

from packet_codec import decode_packet, encode_status
from meshtastic_client import MeshtasticClient


def test_packet_codec() -> None:
    pkt = encode_status("H001", "RED")
    assert pkt == "NS:H001:R"
    assert decode_packet(pkt) == ("H001", "RED")
    assert decode_packet("hello mesh") is None
    print("packet_codec: OK")


def test_mock_fallback() -> None:
    client = MeshtasticClient()
    info = client.connect()
    assert info.mock_mode is True
    assert info.connected is False
    ok, msg = client.send_text("NS:H002:Y")
    assert ok is True
    assert "Mock transmit" in msg
    client.close()
    print("mock_fallback: OK")


if __name__ == "__main__":
    test_packet_codec()
    test_mock_fallback()
    print("All smoke tests passed.")
