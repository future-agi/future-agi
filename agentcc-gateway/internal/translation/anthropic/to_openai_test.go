package anthropic_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

var tr = anthropic.New()

// ─── helpers ──────────────────────────────────────────────────────────────────

func mustJSON(v interface{}) string {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		panic(err)
	}
	return string(b)
}

func msgContent(t *testing.T, content []byte) string {
	t.Helper()
	var s string
	if err := json.Unmarshal(content, &s); err == nil {
		return s
	}
	return string(content)
}

// ─── Simple text message ──────────────────────────────────────────────────────

func TestRequestToCanonical_SimpleText(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 1024,
		"messages": [{"role": "user", "content": "Hello, world!"}]
	}`)

	req, drops, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if len(req.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(req.Messages))
	}
	if req.Messages[0].Role != "user" {
		t.Errorf("expected role=user, got %q", req.Messages[0].Role)
	}
	if msgContent(t, req.Messages[0].Content) != "Hello, world!" {
		t.Errorf("unexpected content: %s", req.Messages[0].Content)
	}
	if req.MaxTokens == nil || *req.MaxTokens != 1024 {
		t.Errorf("expected MaxTokens=1024")
	}
}

// ─── System flattening — bare string ─────────────────────────────────────────

func TestRequestToCanonical_SystemString(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"system": "You are a helpful assistant.",
		"messages": [{"role": "user", "content": "Hi"}]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(req.Messages) != 2 {
		t.Fatalf("expected 2 messages (system + user), got %d", len(req.Messages))
	}
	sys := req.Messages[0]
	if sys.Role != "system" {
		t.Errorf("first message should be system, got %q", sys.Role)
	}
	if msgContent(t, sys.Content) != "You are a helpful assistant." {
		t.Errorf("unexpected system content: %s", sys.Content)
	}
}

// ─── System flattening — array form (single block, no cache_control) ─────────

func TestRequestToCanonical_SystemArraySingleBlock(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"system": [{"type":"text","text":"Be concise."}],
		"messages": [{"role": "user", "content": "Hi"}]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if req.Messages[0].Role != "system" {
		t.Fatalf("expected system message first")
	}
	if msgContent(t, req.Messages[0].Content) != "Be concise." {
		t.Errorf("unexpected system content: %s", req.Messages[0].Content)
	}
}

// ─── System flattening — array with cache_control ────────────────────────────

func TestRequestToCanonical_SystemArrayWithCacheControl(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"system": [
			{"type":"text","text":"Part A.","cache_control":{"type":"ephemeral"}},
			{"type":"text","text":"Part B."}
		],
		"messages": [{"role": "user", "content": "Hi"}]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	sys := req.Messages[0]
	if sys.Role != "system" {
		t.Fatalf("expected system role")
	}
	// Content should be a JSON array of text parts (not a plain string).
	var parts []interface{}
	if err := json.Unmarshal(sys.Content, &parts); err != nil {
		t.Fatalf("expected array content when cache_control present, got: %s", sys.Content)
	}
	if len(parts) != 2 {
		t.Errorf("expected 2 content parts, got %d", len(parts))
	}
}

// ─── Single tool call (assistant) → tool result (user) ───────────────────────

func TestRequestToCanonical_ToolUseAndToolResult(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"messages": [
			{
				"role": "user",
				"content": "What's the weather in London?"
			},
			{
				"role": "assistant",
				"content": [
					{"type":"text","text":"Let me check."},
					{"type":"tool_use","id":"toolu_01","name":"get_weather","input":{"city":"London"}}
				]
			},
			{
				"role": "user",
				"content": [
					{"type":"tool_result","tool_use_id":"toolu_01","content":"15°C, cloudy"}
				]
			}
		]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Expected messages: user, assistant (with tool_calls), tool result, (no extra user)
	// The third Anthropic turn had only a tool_result, so no extra user message.
	if len(req.Messages) < 3 {
		t.Fatalf("expected ≥3 messages, got %d: %s", len(req.Messages), mustJSON(req.Messages))
	}

	// Find the assistant message.
	var asstMsg *struct{ tc []string }
	_ = asstMsg
	found := false
	for _, m := range req.Messages {
		if m.Role == "assistant" {
			found = true
			if len(m.ToolCalls) != 1 {
				t.Errorf("expected 1 tool_call on assistant message, got %d", len(m.ToolCalls))
			}
			tc := m.ToolCalls[0]
			if tc.ID != "toolu_01" {
				t.Errorf("expected tool_call id=toolu_01, got %q", tc.ID)
			}
			if tc.Function.Name != "get_weather" {
				t.Errorf("expected function name=get_weather, got %q", tc.Function.Name)
			}
			if !strings.Contains(tc.Function.Arguments, "London") {
				t.Errorf("expected arguments to contain London, got %q", tc.Function.Arguments)
			}
		}
	}
	if !found {
		t.Error("no assistant message found")
	}

	// Find the tool message.
	foundTool := false
	for _, m := range req.Messages {
		if m.Role == "tool" {
			foundTool = true
			if m.ToolCallID != "toolu_01" {
				t.Errorf("expected tool_call_id=toolu_01, got %q", m.ToolCallID)
			}
			content := msgContent(t, m.Content)
			if !strings.Contains(content, "15°C") {
				t.Errorf("expected tool content to contain '15°C', got %q", content)
			}
		}
	}
	if !foundTool {
		t.Error("no tool message found")
	}
}

// ─── Parallel tool calls ──────────────────────────────────────────────────────

func TestRequestToCanonical_ParallelToolCalls(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"messages": [
			{
				"role": "user",
				"content": "What's the weather in London and Paris?"
			},
			{
				"role": "assistant",
				"content": [
					{"type":"tool_use","id":"toolu_01","name":"get_weather","input":{"city":"London"}},
					{"type":"tool_use","id":"toolu_02","name":"get_weather","input":{"city":"Paris"}}
				]
			},
			{
				"role": "user",
				"content": [
					{"type":"tool_result","tool_use_id":"toolu_01","content":"15°C"},
					{"type":"tool_result","tool_use_id":"toolu_02","content":"22°C"}
				]
			}
		]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Should have: user, assistant (2 tool_calls), tool(toolu_01), tool(toolu_02)
	var toolMsgs []string
	for _, m := range req.Messages {
		if m.Role == "tool" {
			toolMsgs = append(toolMsgs, m.ToolCallID)
		}
	}
	if len(toolMsgs) != 2 {
		t.Errorf("expected 2 tool messages, got %d: %v", len(toolMsgs), toolMsgs)
	}

	// Check assistant has 2 tool calls.
	for _, m := range req.Messages {
		if m.Role == "assistant" {
			if len(m.ToolCalls) != 2 {
				t.Errorf("expected 2 tool_calls, got %d", len(m.ToolCalls))
			}
		}
	}
}

// ─── Vision: base64 ───────────────────────────────────────────────────────────

func TestRequestToCanonical_VisionBase64(t *testing.T) {
	// Use a minimal valid base64 string.
	imgData := "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"messages": [
			{
				"role": "user",
				"content": [
					{"type":"text","text":"Describe this image."},
					{"type":"image","source":{"type":"base64","media_type":"image/png","data":"` + imgData + `"}}
				]
			}
		]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(req.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(req.Messages))
	}
	content := string(req.Messages[0].Content)
	if !strings.Contains(content, "image_url") {
		t.Errorf("expected image_url in content, got: %s", content)
	}
	if !strings.Contains(content, "data:image/png;base64,") {
		t.Errorf("expected data URI in content, got: %s", content)
	}
}

// ─── Vision: URL source ───────────────────────────────────────────────────────

func TestRequestToCanonical_VisionURL(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"messages": [
			{
				"role": "user",
				"content": [
					{"type":"image","source":{"type":"url","url":"https://example.com/image.jpg"}}
				]
			}
		]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	content := string(req.Messages[0].Content)
	if !strings.Contains(content, "https://example.com/image.jpg") {
		t.Errorf("expected URL to pass through, got: %s", content)
	}
}

// ─── Thinking config → Extra ──────────────────────────────────────────────────

func TestRequestToCanonical_ThinkingConfig(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-7-sonnet-20250219",
		"max_tokens": 16000,
		"thinking": {"type":"enabled","budget_tokens":10000},
		"messages": [{"role":"user","content":"Solve this hard math problem."}]
	}`)

	req, drops, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if req.Extra == nil {
		t.Fatal("Extra map is nil")
	}
	thinkingRaw, ok := req.Extra["anthropic_thinking_config"]
	if !ok {
		t.Fatal("anthropic_thinking_config not found in Extra")
	}
	var cfg map[string]interface{}
	if err := json.Unmarshal(thinkingRaw, &cfg); err != nil {
		t.Fatalf("cannot unmarshal thinking config: %v", err)
	}
	if cfg["type"] != "enabled" {
		t.Errorf("expected type=enabled, got %v", cfg["type"])
	}
	if cfg["budget_tokens"].(float64) != 10000 {
		t.Errorf("expected budget_tokens=10000, got %v", cfg["budget_tokens"])
	}
}

// ─── top_k → drop ─────────────────────────────────────────────────────────────

func TestRequestToCanonical_TopKDrop(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"top_k": 40,
		"messages": [{"role":"user","content":"Hello"}]
	}`)

	_, drops, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	found := false
	for _, d := range drops {
		if d == "top_k_unsupported" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected top_k_unsupported in drops, got: %v", drops)
	}
}

// ─── Tool name truncation ─────────────────────────────────────────────────────

func TestRequestToCanonical_ToolNameTruncation(t *testing.T) {
	longName := strings.Repeat("a", 70) // 70 chars, exceeds 64 limit
	body, _ := json.Marshal(map[string]interface{}{
		"model":      "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"tools": []map[string]interface{}{
			{
				"name":         longName,
				"description":  "A long-named tool",
				"input_schema": map[string]interface{}{"type": "object"},
			},
		},
		"messages": []map[string]interface{}{
			{"role": "user", "content": "Use the tool."},
		},
	})

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(req.Tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(req.Tools))
	}
	toolName := req.Tools[0].Function.Name
	if len(toolName) > 64 {
		t.Errorf("tool name not truncated: len=%d", len(toolName))
	}

	// Check that the mapping was stored in Extra.
	if req.Extra == nil {
		t.Fatal("Extra is nil")
	}
	mappingRaw, ok := req.Extra["tool_name_mapping"]
	if !ok {
		t.Fatal("tool_name_mapping not in Extra")
	}
	var mapping map[string]string
	if err := json.Unmarshal(mappingRaw, &mapping); err != nil {
		t.Fatalf("cannot unmarshal tool_name_mapping: %v", err)
	}
	shortName := longName[:64]
	if mapping[shortName] != longName {
		t.Errorf("expected mapping[%q]=%q, got %q", shortName, longName, mapping[shortName])
	}
}

// ─── stop_sequences → stop ───────────────────────────────────────────────────

func TestRequestToCanonical_StopSequences(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 512,
		"stop_sequences": ["END", "STOP"],
		"messages": [{"role":"user","content":"Hello"}]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if req.Stop == nil {
		t.Fatal("expected Stop to be set")
	}
	var stops []string
	if err := json.Unmarshal(req.Stop, &stops); err != nil {
		t.Fatalf("cannot unmarshal Stop: %v", err)
	}
	if len(stops) != 2 || stops[0] != "END" || stops[1] != "STOP" {
		t.Errorf("unexpected stops: %v", stops)
	}
}

// ─── Multi-turn with mixed content ───────────────────────────────────────────

func TestRequestToCanonical_MultiTurn(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 1024,
		"system": "You are helpful.",
		"messages": [
			{"role":"user","content":"Turn 1"},
			{"role":"assistant","content":"Response 1"},
			{"role":"user","content":"Turn 2"},
			{"role":"assistant","content":"Response 2"}
		]
	}`)

	req, _, err := tr.RequestToCanonical(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// system + 4 turns = 5 messages
	if len(req.Messages) != 5 {
		t.Errorf("expected 5 messages, got %d", len(req.Messages))
	}
	if req.Messages[0].Role != "system" {
		t.Errorf("first message should be system")
	}
}

// ─── Malformed JSON ───────────────────────────────────────────────────────────

func TestRequestToCanonical_InvalidJSON(t *testing.T) {
	_, _, err := tr.RequestToCanonical([]byte(`{invalid json`))
	if err == nil {
		t.Error("expected error for invalid JSON")
	}
}
