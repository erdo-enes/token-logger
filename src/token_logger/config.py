from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    confdir: str = ""
    db_path: str = "data/token_logger.db"
    max_queue: int = 1000
    batch_size: int = 50
    flush_interval_sec: float = 0.5
    client_name: str = ""
    redact_secrets: bool = True
    max_body_bytes: int = 2_000_000
    hosts: list[str] = field(default_factory=lambda: [
        "api.anthropic.com", "claude.ai", "api.openai.com", "chatgpt.com",
        "generativelanguage.googleapis.com", "cloudcode-pa.googleapis.com",
        "daily-cloudcode-pa.googleapis.com", "autopush-cloudcode-pa.googleapis.com",
        "api.githubcopilot.com", "copilot-proxy.githubusercontent.com",
        "openrouter.ai", "api.deepseek.com", "api.x.ai", "api.mistral.ai", "api.groq.com",
    ])
    fb_enabled: bool = False
    fb_host: str = "127.0.0.1"
    fb_port: int = 24224
    fb_tag: str = "token_logger.calls"
    fb_format: str = "json"
    fb_max_queue: int = 1000
    upstream_verify: bool = True

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg = cls()
        candidates = [Path(path)] if path else [
            Path(os.environ.get("TOKEN_LOGGER_CONFIG", "config.toml")),
            Path("config.toml"),
        ]
        for candidate in candidates:
            if candidate and candidate.exists():
                with candidate.open("rb") as fh:
                    data = tomllib.load(fh)
                proxy, storage, logging_ = data.get("proxy", {}), data.get("storage", {}), data.get("logging", {})
                fb = data.get("fluentbit", {})
                cfg.listen_host = proxy.get("listen_host", cfg.listen_host)
                cfg.listen_port = int(proxy.get("listen_port", cfg.listen_port))
                cfg.confdir = proxy.get("confdir", cfg.confdir)
                cfg.db_path = storage.get("db_path", cfg.db_path)
                cfg.max_queue = int(storage.get("max_queue", cfg.max_queue))
                cfg.batch_size = int(storage.get("batch_size", cfg.batch_size))
                cfg.flush_interval_sec = float(storage.get("flush_interval_sec", cfg.flush_interval_sec))
                cfg.client_name = logging_.get("client_name", cfg.client_name)
                cfg.redact_secrets = bool(logging_.get("redact_secrets", cfg.redact_secrets))
                cfg.max_body_bytes = int(logging_.get("max_body_bytes", cfg.max_body_bytes))
                cfg.hosts = logging_.get("hosts", cfg.hosts)
                cfg.fb_enabled = bool(fb.get("enabled", cfg.fb_enabled))
                cfg.fb_host = fb.get("host", cfg.fb_host)
                cfg.fb_port = int(fb.get("port", cfg.fb_port))
                cfg.fb_tag = fb.get("tag", cfg.fb_tag)
                cfg.fb_format = fb.get("format", cfg.fb_format)
                cfg.fb_max_queue = int(fb.get("max_queue", cfg.fb_max_queue))
                cfg.upstream_verify = bool(proxy.get("upstream_verify", cfg.upstream_verify))
                break
        cfg.listen_host = os.environ.get("TOKEN_LOGGER_HOST", cfg.listen_host)
        cfg.listen_port = int(os.environ.get("TOKEN_LOGGER_PORT", cfg.listen_port))
        cfg.db_path = os.environ.get("TOKEN_LOGGER_DB", cfg.db_path)
        cfg.client_name = os.environ.get("TOKEN_LOGGER_CLIENT", cfg.client_name)
        cfg.fb_enabled = os.environ.get("TOKEN_LOGGER_FB_ENABLED", str(cfg.fb_enabled)).lower() in ("1", "true", "yes", "on")
        cfg.fb_host = os.environ.get("TOKEN_LOGGER_FB_HOST", cfg.fb_host)
        cfg.fb_port = int(os.environ.get("TOKEN_LOGGER_FB_PORT", cfg.fb_port))
        cfg.fb_tag = os.environ.get("TOKEN_LOGGER_FB_TAG", cfg.fb_tag)
        cfg.fb_format = os.environ.get("TOKEN_LOGGER_FB_FORMAT", cfg.fb_format)
        return cfg
