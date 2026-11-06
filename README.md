# Token Logger — Universal LLM Logging Proxy

Lightweight HTTPS MITM proxy that records LLM API calls from any CLI coding agent (Claude Code, OpenCode, Codex, Aider, Cursor, …) to local SQLite and optionally Fluent Bit. Runs as a background service, zero impact on the CLI when it is down or slow.

## Architecture

```
CLI agent --HTTPS_PROXY=127.0.0.1:8080--> stdlib MITM proxy (proxy.py + certs.py)
                                            |  pass-through, never modify request/response
                                            +--> upstream LLM API (anthropic/openai/…)
                                            |
                                            +--> CompositeWriter (background thread, queue, drop-on-full)
                                                  +--> SQLite (WAL) data/token_logger.db
                                                  +--> Fluent Bit UDP (optional, stdlib only)
```

Lightweight by design:
- **One dependency only:** `cryptography` (for CA/leaf cert generation). Everything else is Python stdlib (`http.client`, `ssl`, `sqlite3`, `socket`). No mitmproxy, no Fluent logger SDK, no msgpack package — the Fluent Forward MessagePack encoder is ~40 lines of stdlib in `fluentbit.py`.
- **Force HTTP/1.1** via ALPN (`http/1.1` only) so the stdlib proxy needs no HTTP/2 stack; Node/Python clients fall back automatically.
- **Runtime:** ~15 MB, <1% CPU idle, one thread per connection + 1 writer thread per sink.
- Trade-off accepted for lightness: no HTTP/2 passthrough, no WebSocket upgrade logging.

## Fluent Bit UDP logger (optional, off by default)

Every record is fanned out to SQLite **and** Fluent Bit when enabled — either sink failing never affects the other or the CLI.

```toml
[fluentbit]
enabled = true
host = "127.0.0.1"
port = 24225
tag = "token_logger.calls"
format = "json"      # "json" (netcat input) or "forward" (MessagePack/FORWARD protocol)
max_queue = 1000
```

- `json`: one `{"tag", "ts", "record"}` object per UDP datagram → Fluent Bit `netcat` input (`Mode udp`, `Parser json`). See `deploy/fluent-bit/fluent-bit.conf`.
- `forward`: real Fluentd Forward MessagePack `[tag, [[epoch, record]]]` over UDP → Fluent Bit `forward` input. UDP payloads capped at 60 KB; oversized `request_json`/`response_json` are truncated (`truncated_for_udp=1`) to fit one datagram.
- Non-blocking `SOCK_DGRAM` socket, queue-full/ send-error = drop + counter. No retries, no DNS blocking beyond first send (IP literal recommended).
- Env overrides: `TOKEN_LOGGER_FB_ENABLED/HOST/PORT/TAG/FORMAT`.

## Zero-impact design (fail-open)

| Risk | Mitigation |
|---|---|
| Logger crashes / DB locked | Proxy handler wraps logging in `try/except`; request/response forwarding is independent |
| Logger slow | Writer queues (default 1000); on full, drop + counter, never block the request thread |
| Fluent Bit down | UDP is fire-and-forget; errors are swallowed, SQLite unaffected |
| Logger down | CLI just loses logging; set proxy env per-session, not system-wide |
| High CPU / IO | systemd `Nice=10` + `IOSchedulingClass=idle`; batch inserts (50 rows / 0.5 s); WAL + `synchronous=NORMAL` |
| Large payloads | `max_body_bytes` cap (2 MB default) for stored copies; forwarded bytes are streamed chunk-by-chunk, never buffered whole |
| Secrets in logs | `redact_secrets=true` masks `sk-…`, `xox…`, `AIza…`; Authorization headers are never stored |
| Service restart | `Restart=on-failure`, `RestartSec=2` (systemd) / NSSM `AppExit=Restart` (Windows) |

## What is recorded (`calls` table)

`ts, client, model, endpoint, host, path, status_code, latency_ms, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens, total_tokens, cost_usd, request_json, response_json, error`

- `request_json`: full parsed body — system prompt, messages/history, user query, tool definitions
- `response_json`: full parsed body — text, `tool_use`/`tool_calls` arguments, `thinking`/`reasoning` blocks; SSE streams stored as `{"stream_events": [...]}`
- Tokens: Anthropic (`input_tokens/output_tokens/cache_*`) and OpenAI (`prompt_tokens/completion_tokens/cached_tokens`) shapes, incl. streaming `usage`
- Cost: prefix-matched pricing table in `src/token_logger/extract.py` (per 1M tokens); unknown model → `NULL`
- Client: `User-Agent` detection (`claude`, `opencode`, `codex`, …) or `TOKEN_LOGGER_CLIENT` / `config.toml` override

## Quick start (dev / foreground)

```powershell
python -m venv .venv; .venv\Scripts\pip install -e .[dev]
Copy-Item config.example.toml config.toml
.venv\Scripts\python -m token_logger.main
```

First run generates `data/mitmproxy/ca.pem`. Trust it, then point a CLI session at the proxy:

```powershell
certutil -addstore Root data\mitmproxy\ca.pem   # Windows, admin
# Linux: sudo cp ca.pem /usr/local/share/ca-certificates/token-logger-ca.crt && sudo update-ca-certificates
$env:HTTPS_PROXY="http://127.0.0.1:8080"; $env:HTTP_PROXY="http://127.0.0.1:8080"
$env:NODE_EXTRA_CA_CERTS="$PWD\data\mitmproxy\ca.pem"   # Node-based CLIs (Claude Code, OpenCode)
$env:SSL_CERT_FILE="$PWD\data\mitmproxy\ca.pem"         # Python-based CLIs
claude   # or: opencode
```

Query:

```powershell
.venv\Scripts\python -m token_logger.query --last 20
sqlite3 data\token_logger.db "SELECT model, SUM(total_tokens), SUM(cost_usd) FROM calls GROUP BY model;"
```

## Background service

### Windows (`deploy/windows/install.ps1`, run as Administrator)
- Preferred: NSSM (handles `pythonw.exe` + SCM stop/restart correctly). Download `nssm.exe` from nssm.cc, put on PATH, re-run.
- Fallback: native `sc.exe create TokenLogger … pythonw -m token_logger.main` with `restart/…` failure actions.
- Auto-start on boot, restart on failure, logs to `data\token-logger.{out,err}.log`.
- Uninstall: `deploy\windows\uninstall.ps1`

### Linux (`deploy/systemd/install.sh`, needs sudo)
- Dedicated `token-logger` system user, `/opt/token-logger`, DB in `/var/lib/token-logger`.
- `systemd` unit: `Restart=on-failure`, `Nice=10`, `ProtectSystem=strict`, `NoNewPrivileges=true`.
- `systemctl status token-logger` / `journalctl -u token-logger -f`

### macOS
- Same logic via LaunchAgent running the venv python; or just run foreground. Not shipped — request if needed.

## Config (`config.toml`, see `config.example.toml`)

`[proxy]` host/port/confdir/upstream_verify · `[storage]` db_path/queue/batch · `[fluentbit]` destination · `[logging]` client_name, redact_secrets, max_body_bytes, LLM host allowlist. Env overrides: `TOKEN_LOGGER_HOST/PORT/DB/CLIENT/CONFIG/FB_*`.

## Tests

```powershell
.venv\Scripts\pytest -q
```

Includes an end-to-end test: local TLS upstream → MITM proxy → SQLite assertion.

## Security notes

- The MITM CA private key (`data/mitmproxy/ca-key.pem`) — treat as secret; anyone with it can intercept TLS.
- Logs contain full prompts/completions (your code, your data). Keep `redact_secrets=true`, restrict DB file ACLs, do not sync `data/` to cloud drives.
- Proxy binds `127.0.0.1` only. Do not expose it on a LAN without auth.
