package main

import (
	"bytes"
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
)

type Usage struct {
	Prompt     *int64 `json:"prompt_tokens,omitempty"`
	Completion *int64 `json:"completion_tokens,omitempty"`
	CacheRead  *int64 `json:"cache_read_tokens,omitempty"`
	CacheWrite *int64 `json:"cache_write_tokens,omitempty"`
	Total      *int64 `json:"total_tokens,omitempty"`
}

var pricing = []struct {
	prefix     string
	in, out    float64
}{
	{"claude-opus", 15.0, 75.0},
	{"claude-sonnet", 3.0, 15.0},
	{"claude-haiku", 0.80, 4.0},
	{"gpt-5", 1.25, 10.0},
	{"gpt-4o", 2.50, 10.0},
	{"gemini-4-argon", 1.25, 5.0},
	{"gemini", 1.25, 5.0},
	{"deepseek", 0.27, 1.10},
	{"llama", 0.20, 0.20},
	{"mistral", 0.20, 0.60},
}

var secretRe = regexp.MustCompile(`sk-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|AIza[0-9A-Za-z_-]{10,}|gh[pousr]_[A-Za-z0-9_]{8,}|ya29\.[0-9A-Za-z_-]{10,}`)

func Redact(s string) string { return secretRe.ReplaceAllString(s, "[REDACTED]") }

func IsLLM(host, path string, hosts []string) bool {
	for _, seg := range []string{"/v1/messages", "/v1/chat/completions", "/v1/responses", "/v1/completions", "/v1beta/models", ":generateContent", ":streamGenerateContent", ":generateChat", ":streamGenerateChat", ":internalAtomicAgenticChat", "/backend-api/codex", "/backend-api/conversation"} {
		if strings.Contains(path, seg) {
			return true
		}
	}
	return false
}

func DetectClient(ua, configured string) string {
	if configured != "" {
		return configured
	}
	lower := strings.ToLower(ua)
	if strings.Contains(lower, "antigravity") || strings.Contains(lower, "agy") {
		return "antigravity"
	}
	for _, name := range []string{"claude", "opencode", "codex", "cursor", "aider", "copilot", "gemini"} {
		if strings.Contains(lower, name) {
			return name
		}
	}
	if len(ua) > 80 {
		return ua[:80]
	}
	return ua
}

func EstimateCost(model string, u Usage) *float64 {
	if model == "" || u.Prompt == nil || u.Completion == nil {
		return nil
	}
	for _, p := range pricing {
		if strings.Contains(strings.ToLower(model), p.prefix) {
			cost := float64(*u.Prompt)/1e6*p.in + float64(*u.Completion)/1e6*p.out
			return &cost
		}
	}
	return nil
}

func intPtr(v int64) *int64 { return &v }

func getInt(m map[string]any, keys ...string) *int64 {
	cur := any(m)
	for _, k := range keys {
		mp, ok := cur.(map[string]any)
		if !ok {
			return nil
		}
		cur, ok = mp[k]
		if !ok {
			return nil
		}
	}
	switch n := cur.(type) {
	case float64:
		return intPtr(int64(n))
	case string:
		if v, err := strconv.ParseInt(n, 10, 64); err == nil {
			return &v
		}
	}
	return nil
}

func getStr(m map[string]any, keys ...string) string {
	cur := any(m)
	for _, k := range keys {
		mp, ok := cur.(map[string]any)
		if !ok {
			return ""
		}
		cur = mp[k]
	}
	if s, ok := cur.(string); ok {
		return s
	}
	return ""
}

func applyUsage(m map[string]any, u *Usage) {
	if v := getInt(m, "usage", "input_tokens"); v != nil {
		u.Prompt = v
	} else if v := getInt(m, "usage", "prompt_tokens"); v != nil {
		u.Prompt = v
	} else if v := getInt(m, "usageMetadata", "promptTokenCount"); v != nil {
		u.Prompt = v
	} else if v := getInt(m, "response", "usageMetadata", "promptTokenCount"); v != nil {
		u.Prompt = v
	}
	if v := getInt(m, "usage", "output_tokens"); v != nil {
		u.Completion = v
	} else if v := getInt(m, "usage", "completion_tokens"); v != nil {
		u.Completion = v
	} else if v := getInt(m, "usageMetadata", "candidatesTokenCount"); v != nil {
		u.Completion = v
	} else if v := getInt(m, "response", "usageMetadata", "candidatesTokenCount"); v != nil {
		u.Completion = v
	}
	if v := getInt(m, "usage", "cache_read_input_tokens"); v != nil {
		u.CacheRead = v
	} else if v := getInt(m, "usage", "prompt_tokens_details", "cached_tokens"); v != nil {
		u.CacheRead = v
	} else if v := getInt(m, "usage", "input_tokens_details", "cached_tokens"); v != nil {
		u.CacheRead = v
	} else if v := getInt(m, "usageMetadata", "cachedContentTokenCount"); v != nil {
		u.CacheRead = v
	} else if v := getInt(m, "response", "usageMetadata", "cachedContentTokenCount"); v != nil {
		u.CacheRead = v
	}
	if v := getInt(m, "usage", "cache_creation_input_tokens"); v != nil {
		u.CacheWrite = v
	}
	if v := getInt(m, "usage", "total_tokens"); v != nil {
		u.Total = v
	} else if v := getInt(m, "usageMetadata", "totalTokenCount"); v != nil {
		u.Total = v
	} else if v := getInt(m, "response", "usageMetadata", "totalTokenCount"); v != nil {
		u.Total = v
	}
	if v := getInt(m, "response", "usage", "input_tokens"); v != nil {
		u.Prompt = v
	}
	if v := getInt(m, "response", "usage", "output_tokens"); v != nil {
		u.Completion = v
	}
	if v := getInt(m, "message", "usage", "input_tokens"); v != nil {
		u.Prompt = v
	}
	if v := getInt(m, "message", "usage", "output_tokens"); v != nil {
		u.Completion = v
	}
}

type Parsed struct {
	Model string
	Usage Usage
	Valid bool
}

func ParseBody(data []byte, contentType string) Parsed {
	var out Parsed
	if len(data) == 0 {
		return out
	}
	if strings.Contains(contentType, "text/event-stream") || bytes.HasPrefix(bytes.TrimSpace(data), []byte("data:")) {
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if !strings.HasPrefix(line, "data:") {
				continue
			}
			payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			if payload == "" || payload == "[DONE]" {
				continue
			}
			var m map[string]any
			if err := json.Unmarshal([]byte(payload), &m); err == nil {
				out.Valid = true
				if v := getStr(m, "model"); v != "" {
					out.Model = v
				} else if v := getStr(m, "modelVersion"); v != "" {
					out.Model = v
				}
				if v := getStr(m, "response", "model"); v != "" {
					out.Model = v
				} else if v := getStr(m, "response", "modelVersion"); v != "" {
					out.Model = v
				} else if v := getStr(m, "message", "model"); v != "" {
					out.Model = v
				}
				applyUsage(m, &out.Usage)
			}
		}
	} else {
		var m map[string]any
		if err := json.Unmarshal(data, &m); err == nil {
			out.Valid = true
			out.Model = getStr(m, "model")
			if out.Model == "" {
				out.Model = getStr(m, "modelVersion")
			}
			if out.Model == "" {
				out.Model = getStr(m, "response", "model")
			}
			if out.Model == "" {
				out.Model = getStr(m, "response", "modelVersion")
			}
			applyUsage(m, &out.Usage)
		}
	}
	if out.Usage.Total == nil && out.Usage.Prompt != nil && out.Usage.Completion != nil {
		out.Usage.Total = intPtr(*out.Usage.Prompt + *out.Usage.Completion)
	}
	return out
}
