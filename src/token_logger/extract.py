from __future__ import annotations

import gzip
import json
import re
import zlib

PRICING_PER_1M: list[tuple[str, float, float]] = [
    ("claude-opus", 15.0, 75.0),
    ("claude-sonnet", 3.0, 15.0),
    ("claude-haiku", 0.80, 4.0),
    ("gpt-5", 1.25, 10.0),
    ("gpt-4o", 2.50, 10.0),
    ("gemini-4-argon", 1.25, 5.0),
    ("gemini", 1.25, 5.0),
    ("deepseek", 0.27, 1.10),
    ("llama", 0.20, 0.20),
    ("mistral", 0.20, 0.60),
]

SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|AIza[0-9A-Za-z_-]{10,}|gh[pousr]_[A-Za-z0-9_]{8,}|ya29\.[0-9A-Za-z_-]{10,})")


def is_llm(host: str, path: str, hosts: list[str]) -> bool:
    return any(seg in path for seg in ("/v1/messages", "/v1/chat/completions", "/v1/responses",
                                       "/v1/completions", "/v1beta/models", ":generateContent",
                                       ":streamGenerateContent", ":generateChat", ":streamGenerateChat",
                                       ":internalAtomicAgenticChat", "/backend-api/codex", "/backend-api/conversation"))


def parse_body(raw: bytes, content_type: str = "", content_encoding: str = "") -> object:
    if not raw:
        return ""
    enc = (content_encoding or "").lower().strip()
    if enc == "gzip" or (len(raw) >= 2 and raw[:2] == b"\x1f\x8b"):
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif enc == "deflate":
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in (content_type or "") or text.startswith("data:"):
        events = []
        for line in text.splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    events.append(line[5:].strip())
        return {"stream_events": events}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _get(d: dict, *keys):
    cur = d
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def extract_usage(parsed) -> dict:
    out = {"prompt_tokens": None, "completion_tokens": None, "cache_read_tokens": None,
           "cache_write_tokens": None, "total_tokens": None}
    if not isinstance(parsed, dict):
        return out
    candidates = [parsed]
    if isinstance(parsed.get("stream_events"), list):
        candidates += [e for e in parsed["stream_events"] if isinstance(e, dict)]
    for data in candidates:
        usage = (data.get("usage") or _get(data, "response", "usage") or _get(data, "message", "usage")
                 or data.get("usageMetadata") or _get(data, "response", "usageMetadata"))
        if not isinstance(usage, dict):
            continue
        out["prompt_tokens"] = usage.get("input_tokens", usage.get("prompt_tokens", usage.get("promptTokenCount", out["prompt_tokens"])))
        out["completion_tokens"] = usage.get("output_tokens", usage.get("completion_tokens", usage.get("candidatesTokenCount", out["completion_tokens"])))
        cached_details = (
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if isinstance(usage.get("prompt_tokens_details"), dict)
            else ((usage.get("input_tokens_details") or {}).get("cached_tokens")
                  if isinstance(usage.get("input_tokens_details"), dict) else None)
        )
        out["cache_read_tokens"] = usage.get("cache_read_input_tokens",
                                             usage.get("cachedContentTokenCount",
                                                       cached_details if cached_details is not None else out["cache_read_tokens"]))
        out["cache_write_tokens"] = usage.get("cache_creation_input_tokens", usage.get("cache_creation", out["cache_write_tokens"]))
        out["total_tokens"] = usage.get("total_tokens", usage.get("totalTokenCount", out["total_tokens"]))
    if out["total_tokens"] is None and out["prompt_tokens"] is not None and out["completion_tokens"] is not None:
        out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    return out


def extract_model(parsed, fallback: str = "") -> str:
    if isinstance(parsed, dict):
        if isinstance(parsed.get("stream_events"), list):
            for ev in parsed["stream_events"]:
                if isinstance(ev, dict):
                    m = (ev.get("model") or ev.get("modelVersion") or _get(ev, "response", "model")
                         or _get(ev, "response", "modelVersion") or _get(ev, "message", "model"))
                    if m:
                        return str(m)
        return str(parsed.get("model") or parsed.get("modelVersion") or _get(parsed, "response", "model")
                   or _get(parsed, "response", "modelVersion") or fallback or "")
    return fallback


def detect_client(user_agent: str, configured: str) -> str:
    if configured:
        return configured
    ua = (user_agent or "").lower()
    if "antigravity" in ua or "agy" in ua:
        return "antigravity"
    for name in ("claude", "opencode", "codex", "cursor", "aider", "copilot", "gemini"):
        if name in ua:
            return name
    return user_agent[:80]


def estimate_cost(model: str, usage: dict) -> float | None:
    if not model or usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None:
        return None
    model_lower = model.lower()
    for prefix, in_price, out_price in PRICING_PER_1M:
        if prefix in model_lower:
            return (usage["prompt_tokens"] / 1_000_000) * in_price + (usage["completion_tokens"] / 1_000_000) * out_price
    return None


def redact(obj, enabled: bool):
    if not enabled:
        return obj
    text = json.dumps(obj, ensure_ascii=False, default=str) if not isinstance(obj, str) else obj
    return SECRET_RE.sub("[REDACTED]", text)
