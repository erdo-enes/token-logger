package main

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	Addr           string   `json:"addr"`
	ConfDir        string   `json:"confdir"`
	JSONLPath      string   `json:"jsonl_path"`
	Client         string   `json:"client_name"`
	MaxBody        int      `json:"max_body_bytes"`
	UpstreamVerify bool     `json:"upstream_verify"`
	FBEnabled      bool     `json:"fb_enabled"`
	FBHost         string   `json:"fb_host"`
	FBPort         int      `json:"fb_port"`
	FBTag          string   `json:"fb_tag"`
	FBFormat       string   `json:"fb_format"`
	Hosts          []string `json:"hosts"`
}

func DefaultConfig() *Config {
	return &Config{
		Addr:           "127.0.0.1:8080",
		ConfDir:        "data/mitmproxy",
		JSONLPath:      "data/calls.jsonl",
		MaxBody:        2_000_000,
		UpstreamVerify: true,
		FBHost:         "127.0.0.1",
		FBPort:         24225,
		FBTag:          "token_logger.calls",
		FBFormat:       "json",
		Hosts: []string{
			"api.anthropic.com",
			"claude.ai",
			"api.openai.com",
			"chatgpt.com",
			"generativelanguage.googleapis.com",
			"cloudcode-pa.googleapis.com",
			"daily-cloudcode-pa.googleapis.com",
			"autopush-cloudcode-pa.googleapis.com",
			"api.githubcopilot.com",
			"copilot-proxy.githubusercontent.com",
			"openrouter.ai",
			"api.deepseek.com",
			"api.x.ai",
			"api.mistral.ai",
			"api.groq.com",
		},
	}
}

func LoadConfig(path string) *Config {
	cfg := DefaultConfig()
	if path == "" {
		path = os.Getenv("TOKEN_LOGGER_CONFIG")
	}
	if path == "" {
		path = "config.json"
	}
	if data, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(data, cfg)
	}
	if v := os.Getenv("TOKEN_LOGGER_ADDR"); v != "" {
		cfg.Addr = v
	}
	if v := os.Getenv("TOKEN_LOGGER_CONFDIR"); v != "" {
		cfg.ConfDir = v
	}
	if v := os.Getenv("TOKEN_LOGGER_JSONL"); v != "" {
		cfg.JSONLPath = v
	}
	if v := os.Getenv("TOKEN_LOGGER_CLIENT"); v != "" {
		cfg.Client = v
	}
	if v := os.Getenv("TOKEN_LOGGER_FB_ENABLED"); v != "" {
		cfg.FBEnabled = v == "1" || strings.EqualFold(v, "true")
	}
	if v := os.Getenv("TOKEN_LOGGER_FB_HOST"); v != "" {
		cfg.FBHost = v
	}
	if v := os.Getenv("TOKEN_LOGGER_FB_PORT"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			cfg.FBPort = n
		}
	}
	if v := os.Getenv("TOKEN_LOGGER_FB_TAG"); v != "" {
		cfg.FBTag = v
	}
	if v := os.Getenv("TOKEN_LOGGER_FB_FORMAT"); v != "" {
		cfg.FBFormat = v
	}
	return cfg
}
