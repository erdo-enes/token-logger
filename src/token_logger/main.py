from __future__ import annotations

import sys
from pathlib import Path

from .certs import CertificateAuthority
from .config import Config
from .fluentbit import FluentBitUDPWriter
from .proxy import TokenProxyServer
from .storage import AsyncWriter, CompositeWriter


def main() -> int:
    config = Config.load(sys.argv[1] if len(sys.argv) > 1 else None)
    confdir = config.confdir or str(Path(config.db_path).expanduser().parent / "mitmproxy")
    writers = [AsyncWriter(config.db_path, config.max_queue, config.batch_size, config.flush_interval_sec)]
    if config.fb_enabled:
        writers.append(FluentBitUDPWriter(config.fb_host, config.fb_port, config.fb_tag,
                                          config.fb_format, config.fb_max_queue))
    writer = CompositeWriter(writers)
    writer.start()
    ca = CertificateAuthority(confdir)
    server = TokenProxyServer((config.listen_host, config.listen_port), config, writer, ca,
                              upstream_verify=config.upstream_verify)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        writer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
