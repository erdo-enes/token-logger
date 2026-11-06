import http.client
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from token_logger.certs import CertificateAuthority
from token_logger.config import Config
from token_logger.proxy import TokenProxyServer
from token_logger.storage import AsyncWriter, CompositeWriter


class Upstream(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"model": "claude-sonnet-4-5", "content": [{"type": "text", "text": "hi"}],
                           "usage": {"input_tokens": 10, "output_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_proxy_logs_llm_call(tmp_path):
    ca = CertificateAuthority(str(tmp_path / "ca"))
    upstream = HTTPServer(("127.0.0.1", 0), Upstream)
    ctx = ca.server_context("127.0.0.1")
    upstream.socket = ctx.wrap_socket(upstream.socket, server_side=True)
    upstream_port = upstream.server_address[1]
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    config = Config()
    config.listen_host = "127.0.0.1"
    config.listen_port = 0
    db = str(tmp_path / "db.sqlite")
    writer = CompositeWriter([AsyncWriter(db)])
    writer.start()
    proxy = TokenProxyServer(("127.0.0.1", 0), config, writer, ca, upstream_verify=False)
    proxy_port = proxy.server_address[1]
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    client_ctx = ssl.create_default_context(cafile=ca.ca_pem)
    conn = http.client.HTTPSConnection("127.0.0.1", proxy_port, context=client_ctx, timeout=10)
    conn.set_tunnel("127.0.0.1", upstream_port)
    conn.request("POST", "/v1/messages", body=json.dumps({"model": "claude-sonnet-4-5", "messages": []}),
                 headers={"Content-Type": "application/json", "User-Agent": "claude-test"})
    resp = conn.getresponse()
    assert resp.status == 200
    resp.read()
    conn.close()
    writer.stop()
    proxy.shutdown()
    upstream.shutdown()

    import sqlite3
    rows = sqlite3.connect(db).execute("SELECT model, total_tokens, client FROM calls").fetchall()
    assert rows == [("claude-sonnet-4-5", 15, "claude")]
