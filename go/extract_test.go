package main

import "testing"

func TestParseAnthropic(t *testing.T) {
	p := ParseBody([]byte(`{"model":"claude-sonnet-4-5","usage":{"input_tokens":100,"output_tokens":50,"cache_read_input_tokens":10,"cache_creation_input_tokens":5}}`), "application/json")
	if p.Model != "claude-sonnet-4-5" || *p.Usage.Prompt != 100 || *p.Usage.Completion != 50 || *p.Usage.CacheRead != 10 || *p.Usage.CacheWrite != 5 || *p.Usage.Total != 150 {
		t.Fatalf("bad parse: %+v", p)
	}
	if EstimateCost(p.Model, p.Usage) == nil {
		t.Fatal("expected cost")
	}
}

func TestParseSSE(t *testing.T) {
	data := []byte("data: {\"model\":\"gpt-4o\",\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":20,\"total_tokens\":30}}\n\ndata: [DONE]\n")
	p := ParseBody(data, "text/event-stream")
	if p.Model != "gpt-4o" || *p.Usage.Total != 30 {
		t.Fatalf("bad sse: %+v", p)
	}
}

func TestIsLLMAndRedact(t *testing.T) {
	if !IsLLM("api.openai.com", "/v1/chat/completions", []string{"api.openai.com"}) {
		t.Fatal("should be llm")
	}
	if IsLLM("example.com", "/other", []string{"api.openai.com"}) {
		t.Fatal("should not be llm")
	}
	if Redact("key sk-abcdefgh12345678") != "key [REDACTED]" {
		t.Fatal("redact failed")
	}
}

func TestMsgpackForwardPrefix(t *testing.T) {
	payload := msgpackEncode([]any{"token_logger.calls", []any{[]any{float64(1), map[string]any{"model": "x"}}}})
	if payload[0] != 0x92 {
		t.Fatalf("expected array(2) header, got %#x", payload[0])
	}
}
