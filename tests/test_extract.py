from token_logger.extract import estimate_cost, extract_model, extract_usage, is_llm, parse_body


def test_anthropic_usage():
    parsed = {"model": "claude-sonnet-4-5", "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}
    usage = extract_usage(parsed)
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 50
    assert usage["cache_read_tokens"] == 10
    assert usage["cache_write_tokens"] == 5
    assert usage["total_tokens"] == 150
    assert extract_model(parsed) == "claude-sonnet-4-5"
    assert estimate_cost("claude-sonnet-4-5", usage) is not None


def test_openai_sse():
    raw = b'data: {"model":"gpt-4o","usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}\n\ndata: [DONE]\n'
    parsed = parse_body(raw, "text/event-stream")
    assert extract_usage(parsed)["total_tokens"] == 30


def test_is_llm():
    hosts = ["api.openai.com"]
    assert is_llm("api.openai.com", "/v1/chat/completions", hosts)
    assert is_llm("chatgpt.com", "/backend-api/codex/responses", [])
    assert not is_llm("example.com", "/other", hosts)
    assert is_llm("example.com", "/v1/chat/completions", [])


def test_gemini_subscription_usage():
    parsed = {"response": {"modelVersion": "gemini-4-argon", "usageMetadata": {
        "promptTokenCount": 6939, "candidatesTokenCount": 6, "cachedContentTokenCount": 80, "totalTokenCount": 6945
    }}}
    usage = extract_usage(parsed)
    assert usage["prompt_tokens"] == 6939
    assert usage["completion_tokens"] == 6
    assert usage["cache_read_tokens"] == 80
    assert usage["total_tokens"] == 6945
    assert extract_model(parsed) == "gemini-4-argon"
