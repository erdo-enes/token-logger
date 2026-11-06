from __future__ import annotations

import json
import queue
import socket
import struct
import threading
import time
from datetime import datetime, timezone

from .storage import CallRecord

MAX_UDP_BYTES = 60_000


def _msgpack(obj) -> bytes:
    if obj is None:
        return b"\xc0"
    if obj is True:
        return b"\xc3"
    if obj is False:
        return b"\xc2"
    if isinstance(obj, int):
        if 0 <= obj < 128:
            return bytes([obj])
        if 0 <= obj < 256:
            return b"\xcc" + struct.pack("B", obj)
        if 0 <= obj < 65536:
            return b"\xcd" + struct.pack(">H", obj)
        if 0 <= obj < 2**32:
            return b"\xce" + struct.pack(">I", obj)
        return b"\xcf" + struct.pack(">Q", obj)
    if isinstance(obj, float):
        return b"\xcb" + struct.pack(">d", obj)
    if isinstance(obj, str):
        raw = obj.encode("utf-8")
        n = len(raw)
        if n < 32:
            return bytes([0xA0 | n]) + raw
        if n < 256:
            return b"\xd9" + struct.pack("B", n) + raw
        if n < 65536:
            return b"\xda" + struct.pack(">H", n) + raw
        return b"\xdb" + struct.pack(">I", n) + raw
    if isinstance(obj, (list, tuple)):
        n = len(obj)
        head = bytes([0x90 | n]) if n < 16 else b"\xdc" + struct.pack(">H", n)
        return head + b"".join(_msgpack(v) for v in obj)
    if isinstance(obj, dict):
        n = len(obj)
        head = bytes([0x80 | n]) if n < 16 else b"\xde" + struct.pack(">H", n)
        return head + b"".join(_msgpack(k) + _msgpack(v) for k, v in obj.items())
    return _msgpack(str(obj))


class FluentBitUDPWriter:
    def __init__(self, host: str = "127.0.0.1", port: int = 24224, tag: str = "token_logger.calls",
                 fmt: str = "json", max_queue: int = 1000, max_bytes: int = MAX_UDP_BYTES):
        self.host = host
        self.port = int(port)
        self.tag = tag
        self.fmt = fmt.lower()
        self.max_bytes = max_bytes
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="token-logger-fluentbit", daemon=True)
        self.dropped = 0
        self.sent = 0
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._thread.start()

    def put(self, record: CallRecord) -> None:
        try:
            self.queue.put_nowait(record.as_row())
        except queue.Full:
            self.dropped += 1

    def build_payload(self, row: dict) -> bytes:
        row = dict(row)
        if self.fmt == "forward":
            try:
                ts = datetime.fromisoformat(row.get("ts", "")).timestamp()
            except ValueError:
                ts = time.time()
            payload = _msgpack([self.tag, [[ts, row]]])
        else:
            payload = json.dumps({"tag": self.tag, "ts": row.get("ts", ""), "record": row},
                                 ensure_ascii=False, default=str).encode("utf-8")
        if len(payload) > self.max_bytes:
            for key in ("request_json", "response_json"):
                if row.get(key):
                    row[key] = str(row[key])[:8000]
            row["truncated_for_udp"] = 1
            if self.fmt == "forward":
                try:
                    ts = datetime.fromisoformat(row.get("ts", "")).timestamp()
                except ValueError:
                    ts = time.time()
                payload = _msgpack([self.tag, [[ts, row]]])
            else:
                payload = json.dumps({"tag": self.tag, "ts": row.get("ts", ""), "record": row},
                                     ensure_ascii=False, default=str).encode("utf-8")
            payload = payload[: self.max_bytes]
        return payload

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set() or not self.queue.empty():
            try:
                row = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._sock.sendto(self.build_payload(row), (self.host, self.port))
                self.sent += 1
            except OSError:
                self.dropped += 1

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
