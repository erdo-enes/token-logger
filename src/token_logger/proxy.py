from __future__ import annotations

import http.client
import ssl
import time
from datetime import datetime, timezone
from socketserver import BaseRequestHandler, ThreadingTCPServer

from .certs import CertificateAuthority
from .config import Config
from .extract import detect_client, estimate_cost, extract_model, extract_usage, is_llm, parse_body, redact
from .storage import CallRecord, dump


def _read_chunked(rfile) -> bytes:
    chunks = []
    while True:
        line = rfile.readline(65536).strip()
        if not line:
            break
        size = int(line.split(b";")[0], 16)
        if size == 0:
            rfile.readline(65536)
            break
        chunks.append(rfile.read(size))
        rfile.readline(65536)
    return b"".join(chunks)


def _read_body(rfile, headers: dict) -> bytes:
    if headers.get("transfer-encoding", "").lower() == "chunked":
        return _read_chunked(rfile)
    length = int(headers.get("content-length", "0") or 0)
    return rfile.read(length) if length else b""


def _send_chunked(sock, data: bytes) -> None:
    sock.sendall(f"{len(data):x}\r\n".encode() + data + b"\r\n")


class _Handler(BaseRequestHandler):
    config: Config
    writer = None
    ca: CertificateAuthority | None = None
    upstream_verify: bool = True

    def handle(self):
        try:
            self.request.settimeout(300)
            rfile = self.request.makefile("rb")
            line = rfile.readline(65536)
            if not line:
                return
            parts = line.decode("iso-8859-1").strip().split()
            if len(parts) < 3:
                return
            method, target, _version = parts
            if method.upper() == "CONNECT":
                host, _, port_s = target.rpartition(":")
                port = int(port_s or 443)
                self._read_headers(rfile)
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                h_lower = host.lower()
                should_intercept = h_lower in ("127.0.0.1", "localhost", "::1") or any(
                    h_lower == a.lower() or h_lower.endswith("." + a.lower()) for a in self.config.hosts
                )
                if not should_intercept:
                    self._tunnel_blind(host, port)
                    return
                context = self.ca.server_context(host)
                tls_sock = context.wrap_socket(self.request, server_side=True)
                self.request = tls_sock
                rfile = tls_sock.makefile("rb")
                self._serve_requests(rfile, host, port, tls=True)
            else:
                host, port, path = self._split_target(target, rfile)
                self._serve_requests(rfile, host, port, tls=False, first=(method, path, line))
        except Exception:
            import os, traceback
            if os.environ.get("TOKEN_LOGGER_DEBUG"):
                traceback.print_exc()

    def _tunnel_blind(self, host: str, port: int) -> None:
        import select
        import socket
        try:
            up = socket.create_connection((host, port), timeout=15)
        except OSError:
            return
        try:
            sockets = [self.request, up]
            while True:
                r, _, _ = select.select(sockets, [], [], 60)
                if not r:
                    break
                for s in r:
                    data = s.recv(32768)
                    if not data:
                        return
                    other = up if s is self.request else self.request
                    other.sendall(data)
        except OSError:
            pass
        finally:
            try:
                up.close()
            except OSError:
                pass

    def _split_target(self, target: str, rfile):
        headers = self._read_headers(rfile)
        self._pending_headers = headers
        if target.startswith("http://") or target.startswith("https://"):
            rest = target.split("://", 1)[1]
            hostport, _, path = rest.partition("/")
            host, _, port_s = hostport.rpartition(":")
            return host, int(port_s or (443 if target.startswith("https") else 80)), "/" + path
        return headers.get("host", "").split(":")[0], 80, target

    def _read_headers(self, rfile) -> dict:
        headers = {}
        raw_headers = []
        while True:
            line = rfile.readline(65536)
            if not line or line in (b"\r\n", b"\n"):
                break
            if b":" in line:
                key, _, value = line.decode("iso-8859-1").partition(":")
                k_clean, v_clean = key.strip(), value.strip()
                headers[k_clean.lower()] = v_clean
                raw_headers.append((k_clean, v_clean))
        self._raw_headers = raw_headers
        return headers

    def _serve_requests(self, rfile, default_host: str, default_port: int, tls: bool, first=None):
        pending = first
        while True:
            if pending:
                method, path, _line = pending
                headers = getattr(self, "_pending_headers", {})
                pending = None
            else:
                line = rfile.readline(65536)
                if not line:
                    return
                parts = line.decode("iso-8859-1").strip().split()
                if len(parts) < 3:
                    return
                method, path, _version = parts
                if path.startswith("http://") or path.startswith("https://"):
                    _h, _p, path = self._split_target(path, rfile)
                    headers = getattr(self, "_pending_headers", {})
                else:
                    headers = self._read_headers(rfile)
            if headers.get("expect", "").lower() == "100-continue":
                self.request.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
            started = time.time()
            raw_body = _read_body(rfile, headers)
            host = headers.get("host", default_host).split(":")[0] or default_host
            port = default_port
            if ":" in headers.get("host", ""):
                try:
                    port = int(headers["host"].rpartition(":")[2])
                except ValueError:
                    pass
            is_tls_upstream = tls or port == 443
            parsed_req = parse_body(raw_body[: self.config.max_body_bytes],
                                    headers.get("content-type", ""),
                                    headers.get("content-encoding", "")) if is_llm(host, path, self.config.hosts) else ""
            close_after = headers.get("connection", "").lower() == "close" or headers.get("proxy-connection", "").lower() == "close"
            try:
                resp, resp_headers, status, reason = self._forward(
                    method, path, raw_body, headers, host, port, is_tls_upstream)
            except Exception:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            use_chunked = "chunked" in resp_headers.get("transfer-encoding", "").lower() or "content-length" not in resp_headers
            out_headers = {k: v for k, v in resp_headers.items()
                           if k.lower() not in ("transfer-encoding", "connection", "content-length")}
            if use_chunked:
                out_headers["Transfer-Encoding"] = "chunked"
            else:
                out_headers["Content-Length"] = resp_headers["content-length"]
            if close_after:
                out_headers["Connection"] = "close"
            header_blob = f"HTTP/1.1 {status} {reason}\r\n" + "".join(
                f"{k}: {v}\r\n" for k, v in out_headers.items()) + "\r\n"
            self.request.sendall(header_blob.encode("iso-8859-1"))
            captured = bytearray()
            try:
                while True:
                    data = resp.read1(16384) if hasattr(resp, "read1") else resp.read(16384)
                    if not data:
                        break
                    if len(captured) < self.config.max_body_bytes:
                        captured.extend(data[: self.config.max_body_bytes - len(captured)])
                    if use_chunked:
                        _send_chunked(self.request, data)
                    else:
                        self.request.sendall(data)
                if use_chunked:
                    self.request.sendall(b"0\r\n\r\n")
            finally:
                resp.close()
            if is_llm(host, path, self.config.hosts):
                try:
                    parsed_resp = parse_body(bytes(captured), resp_headers.get("content-type", ""),
                                             resp_headers.get("content-encoding", ""))
                    usage = extract_usage(parsed_resp)
                    model = extract_model(parsed_resp, extract_model(parsed_req) if isinstance(parsed_req, dict) else "")
                    self.writer.put(CallRecord(
                        ts=datetime.now(timezone.utc).isoformat(),
                        client=detect_client(headers.get("user-agent", ""), self.config.client_name),
                        model=model,
                        endpoint=f"https://{host}{path.split('?')[0]}" if is_tls_upstream else f"http://{host}{path.split('?')[0]}",
                        host=host, path=path.split("?")[0], status_code=status,
                        latency_ms=round((time.time() - started) * 1000, 1),
                        cost_usd=estimate_cost(model, usage),
                        request_json=dump(redact(parsed_req, self.config.redact_secrets)),
                        response_json=dump(redact(parsed_resp, self.config.redact_secrets)),
                        **usage,
                    ))
                except Exception:
                    pass
            if close_after:
                return

    def _forward(self, method, path, body, headers, host, port, use_tls):
        skip = ("proxy-connection", "proxy-authorization", "connection",
                "transfer-encoding", "host", "content-length", "expect")
        raw_list = getattr(self, "_raw_headers", None)
        if raw_list is not None:
            forward_headers = {k: v for k, v in raw_list if k.lower() not in skip}
        else:
            forward_headers = {k: v for k, v in headers.items() if k.lower() not in skip}
        if body:
            forward_headers["Content-Length"] = str(len(body))
        has_host = any(k.lower() == "host" for k in forward_headers)
        has_accept_enc = any(k.lower() == "accept-encoding" for k in forward_headers)
        if use_tls:
            context = ssl.create_default_context() if self.upstream_verify else ssl._create_unverified_context()
            conn = http.client.HTTPSConnection(host, port, timeout=300, context=context)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=300)
        conn.putrequest(method, path, skip_host=has_host, skip_accept_encoding=has_accept_enc)
        for k, v in forward_headers.items():
            conn.putheader(k, v)
        conn.endheaders(message_body=body or None)
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp, resp_headers, resp.status, resp.reason


class TokenProxyServer(ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config: Config, writer, ca: CertificateAuthority, upstream_verify: bool = True):
        _Handler.config = config
        _Handler.writer = writer
        _Handler.ca = ca
        _Handler.upstream_verify = upstream_verify
        super().__init__(address, _Handler)
