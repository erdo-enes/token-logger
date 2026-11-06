import json

from token_logger.fluentbit import FluentBitUDPWriter, _msgpack
from token_logger.storage import CallRecord


def _record():
    return CallRecord(ts="2026-01-01T00:00:00+00:00", model="claude-sonnet-4-5", total_tokens=3)


def test_json_payload():
    writer = FluentBitUDPWriter(fmt="json")
    payload = writer.build_payload(_record().as_row())
    data = json.loads(payload)
    assert data["tag"] == "token_logger.calls"
    assert data["record"]["model"] == "claude-sonnet-4-5"


def test_forward_msgpack():
    writer = FluentBitUDPWriter(fmt="forward")
    payload = writer.build_payload(_record().as_row())
    assert payload[0] == 0x92
    assert b"token_logger.calls" in payload
    assert b"claude-sonnet-4-5" in payload


def test_msgpack_scalars():
    assert _msgpack(None) == b"\xc0"
    assert _msgpack(True) == b"\xc3"
    assert _msgpack(1) == b"\x01"
    assert _msgpack("a") == b"\xa1a"


def test_udp_send_does_not_block(monkeypatch):
    sent = []
    class FakeSock:
        def setblocking(self, _): pass
        def sendto(self, data, addr): sent.append((data, addr))
        def close(self): pass
    monkeypatch.setattr("token_logger.fluentbit.socket.socket", lambda *a, **k: FakeSock())
    writer = FluentBitUDPWriter(host="127.0.0.1", port=24224)
    writer.start()
    writer.put(_record())
    writer.stop()
    assert len(sent) == 1
    assert sent[0][1] == ("127.0.0.1", 24224)


def test_oversize_truncates():
    writer = FluentBitUDPWriter(fmt="json", max_bytes=5000)
    record = CallRecord(ts="2026-01-01T00:00:00+00:00", request_json="x" * 200000)
    payload = writer.build_payload(record.as_row())
    assert len(payload) <= 5000
