# Token Logger — Zero-Dependency Go LLM Logging Proxy

A lightweight, fail-open, zero-modification HTTPS logging proxy written in 100% Go standard library (`CGO_ENABLED=0`, zero external dependencies).

Records LLM API calls from any CLI coding agent (**Claude Code**, **Antigravity (`agy`)**, **Codex CLI**, **OpenCode**, **Cursor**, **Aider**, **GitHub Copilot**) across both **subscriptions (OAuth)** and API keys, while remaining completely transparent to upstream providers.

| Metric | Value |
|---|---|
| Binary size | **~7 MB single static executable** (`CGO_ENABLED=0`, no runtime) |
| Idle memory | **~5 MB RSS** |
| Per-request overhead | **<0.5 ms** (streaming pass-through tee + HTTP/2 connection pool) |
| Storage | Local JSONL (`data/calls.jsonl`) + optional Fluent Bit UDP (JSON or MessagePack Forward) |

---

## Prebuilt Binaries (`go/bin/`)

Pre-compiled static binaries are included in [`go/bin/`](go/bin/) and attached to GitHub Releases:
- **Windows (`amd64`):** `go/bin/token-logger.exe`
- **Linux (`amd64`):** `go/bin/token-logger-linux-amd64`
- **macOS (`arm64`):** `go/bin/token-logger` / `go/bin/token-logger-darwin-arm64`

To rebuild all binaries from source:
```bash
./go/build.sh      # macOS / Linux (cross-compiles Windows, Linux, macOS)
# or on Windows:
.\go\build.ps1
```

---

## Stealth & Zero-Impact Design

1. **Zero Request/Header Modification:**
   - Never injects `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `Via`, or `Accept-Encoding`.
   - Streams request and response bodies directly without modifying bytes or re-serializing JSON.
2. **Selective Blind TCP Tunneling:**
   - Only intercepts configured LLM domains (`api.anthropic.com`, `chatgpt.com`, `cloudcode-pa.googleapis.com`, `api.openai.com`, etc.).
   - All other domains (OAuth login endpoints, telemetry, GitHub, package registries) pass through as a blind raw TCP tunnel (`io.Copy`) with zero TLS interception.
3. **Native HTTP/2 & Connection Pooling:**
   - Negotiates `h2` and `http/1.1` with persistent upstream connection reuse.
4. **Fail-Open Logging:**
   - Non-blocking buffered writer channel (`1000` capacity) and fire-and-forget UDP sink ensure your coding CLI is never slowed down or blocked.

---

## Quick Start

### 1. Start the Proxy
```powershell
# Windows
Copy-Item go\config.example.json go\config.json
.\go\bin\token-logger.exe go\config.json
```
```bash
# macOS / Linux
cp go/config.example.json go/config.json
./go/bin/token-logger go/config.json
```

### 2. Trust the Local Root CA (One-Time Setup)
First run generates `data/mitmproxy/ca.pem`. Add it to your OS trust store so CLIs verify both blind-tunneled and intercepted connections seamlessly:
```powershell
# Windows (Run as Administrator)
certutil -addstore Root data\mitmproxy\ca.pem
```
```bash
# Linux
sudo cp data/mitmproxy/ca.pem /usr/local/share/ca-certificates/local-root-ca.crt && sudo update-ca-certificates
```

### 3. Point Your CLI Session at the Proxy
```powershell
$env:HTTPS_PROXY="http://127.0.0.1:8080"
$env:NODE_EXTRA_CA_CERTS="$PWD\data\mitmproxy\ca.pem"   # For Node-based CLIs (Claude Code, OpenCode)
```

---

## Background Service Installation

- **Windows Service (Administrator):**
  - Install: `.\go\deploy\install-windows.ps1` (uses NSSM or `sc.exe`)
  - Uninstall: `.\go\deploy\uninstall-windows.ps1`
- **Linux (`systemd`):**
  - Service unit: `go/deploy/token-logger-go.service`

---

## Fluent Bit / Remote Logger Server (Optional)

Enable `"fb_enabled": true` in `config.json` (or `TOKEN_LOGGER_FB_ENABLED=true`) to stream every record over UDP in `"json"` or Fluentd `"forward"` (MessagePack) format to a local or remote Fluent Bit / Fluentd / Vector collector (see `deploy/fluent-bit/fluent-bit.conf`).

---

## Tests

```bash
cd go && go test -v ./...
```
