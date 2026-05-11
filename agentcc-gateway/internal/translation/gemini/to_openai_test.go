package gemini_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/translation/gemini"
)

func translator() *gemini.Translator { return gemini.New() }

// ── helpers ──────────────────────────────────────────────────────────────────

func mustMarshal(t *testing.T, v interface{}) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("mustMarshal: %v", err)
	}
	return b
}

func contentStr(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	if raw == nil {
		return ""
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	return string(raw)
}

// ── tests ────────────────────────────────────────────────────────────────────

func TestRequestToCanonical_SimpleUserMessage(t *testing.T) {
	input := `{
		"contents": [
			{"role": "user", "parts": [{"text": "Hello, world!"}]}
		]
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if len(req.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(req.Messages))
	}
	msg := req.Messages[0]
	if msg.Role != "user" {
		t.Errorf("role: got %q, want %q", msg.Role, "user")
	}
	if contentStr(t, msg.Content) != "Hello, world!" {
		t.Errorf("content: got %q, want %q", contentStr(t, msg.Content), "Hello, world!")
	}
}

func TestRequestToCanonical_MultiTurnConversation(t *testing.T) {
	input := `{
		"contents": [
			{"role": "user",  "parts": [{"text": "What is 2+2?"}]},
			{"role": "model", "parts": [{"text": "4"}]},
			{"role": "user",  "parts": [{"text": "And 3+3?"}]}
		]
	}`
	tr := translator()
	req, _, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(req.Messages) != 3 {
		t.Fatalf("expected 3 messages, got %d", len(req.Messages))
	}
	roles := []string{"user", "assistant", "user"}
	texts := []string{"What is 2+2?", "4", "And 3+3?"}
	for i, msg := range req.Messages {
		if msg.Role != roles[i] {
			t.Errorf("msg[%d].Role: got %q, want %q", i, msg.Role, roles[i])
		}
		if contentStr(t, msg.Content) != texts[i] {
			t.Errorf("msg[%d].Content: got %q, want %q", i, contentStr(t, msg.Content), texts[i])
		}
	}
}

func TestRequestToCanonical_SystemInstruction(t *testing.T) {
	input := `{
		"systemInstruction": {
			"parts": [{"text": "You are a helpful assistant."}]
		},
		"contents": [
			{"role": "user", "parts": [{"text": "Hi"}]}
		]
	}`
	tr := translator()
	req, _, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(req.Messages) != 2 {
		t.Fatalf("expected 2 messages (system + user), got %d", len(req.Messages))
	}
	if req.Messages[0].Role != "system" {
		t.Errorf("first message role: got %q, want %q", req.Messages[0].Role, "system")
	}
	if contentStr(t, req.Messages[0].Content) != "You are a helpful assistant." {
		t.Errorf("system content: %q", contentStr(t, req.Messages[0].Content))
	}
	if req.Messages[1].Role != "user" {
		t.Errorf("second message role: got %q, want %q", req.Messages[1].Role, "user")
	}
}

func TestRequestToCanonical_FunctionCallGeneratesIDs(t *testing.T) {
	input := `{
		"contents": [
			{
				"role": "model",
				"parts": [{
					"functionCall": {
						"name": "get_weather",
						"args": {"location": "London"}
					}
				}]
			}
		]
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if len(req.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(req.Messages))
	}
	msg := req.Messages[0]
	if msg.Role != "assistant" {
		t.Errorf("role: got %q, want %q", msg.Role, "assistant")
	}
	if len(msg.ToolCalls) != 1 {
		t.Fatalf("expected 1 tool call, got %d", len(msg.ToolCalls))
	}
	tc := msg.ToolCalls[0]
	if !strings.HasPrefix(tc.ID, "call_") {
		t.Errorf("tool call ID should start with call_, got %q", tc.ID)
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("tool call name: got %q, want %q", tc.Function.Name, "get_weather")
	}
	// Verify the ID is stored in Extra
	if req.Extra == nil {
		t.Fatal("expected Extra to be set with gemini_tool_call_id_map")
	}
	mapRaw, ok := req.Extra["gemini_tool_call_id_map"]
	if !ok {
		t.Fatal("expected gemini_tool_call_id_map in Extra")
	}
	var idMap map[string][]string
	if err := json.Unmarshal(mapRaw, &idMap); err != nil {
		t.Fatalf("unmarshal id map: %v", err)
	}
	if ids := idMap["get_weather"]; len(ids) != 1 || ids[0] != tc.ID {
		t.Errorf("id map mismatch: map has %v, tool call has %q", ids, tc.ID)
	}
}

func TestRequestToCanonical_FunctionResponseRoundTrip(t *testing.T) {
	// Model calls get_weather, user replies with function result.
	// The functionResponse should get the same ID as the functionCall.
	input := `{
		"contents": [
			{
				"role": "model",
				"parts": [{
					"functionCall": {
						"name": "get_weather",
						"args": {"location": "London"}
					}
				}]
			},
			{
				"role": "user",
				"parts": [{
					"functionResponse": {
						"name": "get_weather",
						"response": {"result": "Sunny, 22°C"}
					}
				}]
			}
		]
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	// Expected messages: [assistant(tool_calls), tool]
	if len(req.Messages) != 2 {
		t.Fatalf("expected 2 messages, got %d: %+v", len(req.Messages), req.Messages)
	}
	assistantMsg := req.Messages[0]
	if assistantMsg.Role != "assistant" || len(assistantMsg.ToolCalls) != 1 {
		t.Fatalf("expected assistant message with 1 tool call")
	}
	toolMsg := req.Messages[1]
	if toolMsg.Role != "tool" {
		t.Errorf("expected tool role, got %q", toolMsg.Role)
	}
	// IDs must match
	if toolMsg.ToolCallID != assistantMsg.ToolCalls[0].ID {
		t.Errorf("tool_call_id mismatch: tool has %q, call has %q",
			toolMsg.ToolCallID, assistantMsg.ToolCalls[0].ID)
	}
}

func TestRequestToCanonical_InlineImage(t *testing.T) {
	input := `{
		"contents": [{
			"role": "user",
			"parts": [
				{"text": "What is in this image?"},
				{"inlineData": {"mimeType": "image/png", "data": "iVBORw0KGgo="}}
			]
		}]
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if len(req.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(req.Messages))
	}
	msg := req.Messages[0]
	// Content should be a multipart array
	var parts []map[string]interface{}
	if err := json.Unmarshal(msg.Content, &parts); err != nil {
		t.Fatalf("expected multipart content, got: %s", msg.Content)
	}
	foundText := false
	foundImage := false
	for _, p := range parts {
		switch p["type"] {
		case "text":
			foundText = true
		case "image_url":
			foundImage = true
			imgURL, _ := p["image_url"].(map[string]interface{})
			url, _ := imgURL["url"].(string)
			if !strings.HasPrefix(url, "data:image/png;base64,") {
				t.Errorf("image url should be data URI, got %q", url)
			}
		}
	}
	if !foundText {
		t.Error("expected text part in multipart content")
	}
	if !foundImage {
		t.Error("expected image_url part in multipart content")
	}
}

func TestRequestToCanonical_FileDataImage(t *testing.T) {
	input := `{
		"contents": [{
			"role": "user",
			"parts": [{
				"fileData": {"mimeType": "image/jpeg", "fileUri": "https://example.com/photo.jpg"}
			}]
		}]
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	msg := req.Messages[0]
	var parts []map[string]interface{}
	if err := json.Unmarshal(msg.Content, &parts); err != nil {
		t.Fatalf("expected multipart content, got: %s", msg.Content)
	}
	foundImage := false
	for _, p := range parts {
		if p["type"] == "image_url" {
			foundImage = true
			imgURL, _ := p["image_url"].(map[string]interface{})
			url, _ := imgURL["url"].(string)
			if url != "https://example.com/photo.jpg" {
				t.Errorf("expected file URI passthrough, got %q", url)
			}
		}
	}
	if !foundImage {
		t.Error("expected image_url part for image fileData")
	}
}

func TestRequestToCanonical_FileDataNonImage_Drop(t *testing.T) {
	input := `{
		"contents": [{
			"role": "user",
			"parts": [{
				"fileData": {"mimeType": "video/mp4", "fileUri": "gs://bucket/video.mp4"}
			}]
		}]
	}`
	tr := translator()
	_, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	found := false
	for _, d := range drops {
		if d == "non_image_filedata_unsupported" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected non_image_filedata_unsupported drop, got: %v", drops)
	}
}

func TestRequestToCanonical_ToolsAndToolConfig(t *testing.T) {
	input := `{
		"contents": [{"role": "user", "parts": [{"text": "What is the weather?"}]}],
		"tools": [{
			"functionDeclarations": [{
				"name": "get_weather",
				"description": "Get current weather",
				"parameters": {"type": "object", "properties": {"location": {"type": "string"}}}
			}]
		}],
		"toolConfig": {
			"functionCallingConfig": {"mode": "AUTO"}
		}
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if len(req.Tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(req.Tools))
	}
	tool := req.Tools[0]
	if tool.Type != "function" {
		t.Errorf("tool type: got %q, want %q", tool.Type, "function")
	}
	if tool.Function.Name != "get_weather" {
		t.Errorf("tool name: got %q, want %q", tool.Function.Name, "get_weather")
	}
	// tool_choice should be "auto"
	var choice string
	if err := json.Unmarshal(req.ToolChoice, &choice); err != nil {
		t.Fatalf("unmarshal tool_choice: %v", err)
	}
	if choice != "auto" {
		t.Errorf("tool_choice: got %q, want %q", choice, "auto")
	}
}

func TestRequestToCanonical_ToolConfigModes(t *testing.T) {
	cases := []struct {
		mode     string
		expected string
	}{
		{"AUTO", "auto"},
		{"ANY", "required"},
		{"NONE", "none"},
	}
	for _, c := range cases {
		input := `{
			"contents": [{"role":"user","parts":[{"text":"hi"}]}],
			"toolConfig": {"functionCallingConfig": {"mode": "` + c.mode + `"}}
		}`
		tr := translator()
		req, _, err := tr.RequestToCanonical([]byte(input))
		if err != nil {
			t.Fatalf("mode %s: unexpected error: %v", c.mode, err)
		}
		var choice string
		if err := json.Unmarshal(req.ToolChoice, &choice); err != nil {
			t.Fatalf("mode %s: unmarshal tool_choice: %v", c.mode, err)
		}
		if choice != c.expected {
			t.Errorf("mode %s: tool_choice got %q, want %q", c.mode, choice, c.expected)
		}
	}
}

func TestRequestToCanonical_GenerationConfig(t *testing.T) {
	temp := 0.7
	topP := 0.9
	maxTokens := 1024
	candidateCount := 1
	input := mustMarshal(t, map[string]interface{}{
		"contents": []map[string]interface{}{
			{"role": "user", "parts": []map[string]interface{}{{"text": "hi"}}},
		},
		"generationConfig": map[string]interface{}{
			"temperature":     temp,
			"topP":            topP,
			"topK":            40,
			"maxOutputTokens": maxTokens,
			"candidateCount":  candidateCount,
			"stopSequences":   []string{"END", "STOP"},
		},
	})
	tr := translator()
	req, drops, err := tr.RequestToCanonical(input)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// topK → drop
	foundTopK := false
	for _, d := range drops {
		if d == "top_k_unsupported" {
			foundTopK = true
		}
	}
	if !foundTopK {
		t.Error("expected top_k_unsupported drop")
	}
	if req.Temperature == nil || *req.Temperature != temp {
		t.Errorf("temperature: got %v, want %v", req.Temperature, temp)
	}
	if req.TopP == nil || *req.TopP != topP {
		t.Errorf("top_p: got %v, want %v", req.TopP, topP)
	}
	if req.MaxTokens == nil || *req.MaxTokens != maxTokens {
		t.Errorf("max_tokens: got %v, want %v", req.MaxTokens, maxTokens)
	}
	if req.N == nil || *req.N != candidateCount {
		t.Errorf("n: got %v, want %v", req.N, candidateCount)
	}
	var stops []string
	if err := json.Unmarshal(req.Stop, &stops); err != nil {
		t.Fatalf("stop: unmarshal error: %v", err)
	}
	if len(stops) != 2 || stops[0] != "END" || stops[1] != "STOP" {
		t.Errorf("stop sequences: got %v", stops)
	}
}

func TestRequestToCanonical_ResponseMimeTypeJSON(t *testing.T) {
	input := `{
		"contents": [{"role":"user","parts":[{"text":"hi"}]}],
		"generationConfig": {
			"responseMimeType": "application/json"
		}
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if req.ResponseFormat == nil {
		t.Fatal("expected ResponseFormat to be set")
	}
	if req.ResponseFormat.Type != "json_object" {
		t.Errorf("ResponseFormat.Type: got %q, want %q", req.ResponseFormat.Type, "json_object")
	}
}

func TestRequestToCanonical_ResponseMimeTypeJSONWithSchema(t *testing.T) {
	schema := `{"type":"object","properties":{"name":{"type":"string"}}}`
	input := `{
		"contents": [{"role":"user","parts":[{"text":"hi"}]}],
		"generationConfig": {
			"responseMimeType": "application/json",
			"responseSchema": ` + schema + `
		}
	}`
	tr := translator()
	req, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(drops) != 0 {
		t.Errorf("unexpected drops: %v", drops)
	}
	if req.ResponseFormat == nil {
		t.Fatal("expected ResponseFormat to be set")
	}
	if req.ResponseFormat.Type != "json_schema" {
		t.Errorf("ResponseFormat.Type: got %q, want %q", req.ResponseFormat.Type, "json_schema")
	}
	if len(req.ResponseFormat.JSONSchema) == 0 {
		t.Error("expected JSONSchema to be non-empty")
	}
}

func TestRequestToCanonical_ResponseModalities_Drop(t *testing.T) {
	input := `{
		"contents": [{"role":"user","parts":[{"text":"hi"}]}],
		"generationConfig": {
			"responseModalities": ["AUDIO"]
		}
	}`
	tr := translator()
	_, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	found := false
	for _, d := range drops {
		if d == "response_modalities_unsupported" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected response_modalities_unsupported drop, got: %v", drops)
	}
}

func TestRequestToCanonical_SafetySettings_Drop(t *testing.T) {
	input := `{
		"contents": [{"role":"user","parts":[{"text":"hi"}]}],
		"safetySettings": [{"category":"HARM_CATEGORY_HARASSMENT","threshold":"BLOCK_MEDIUM_AND_ABOVE"}]
	}`
	tr := translator()
	_, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	found := false
	for _, d := range drops {
		if d == "safety_settings_unsupported" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected safety_settings_unsupported drop, got: %v", drops)
	}
}

func TestRequestToCanonical_CachedContent_Drop(t *testing.T) {
	input := `{
		"contents": [{"role":"user","parts":[{"text":"hi"}]}],
		"cachedContent": "cachedContents/abc123"
	}`
	tr := translator()
	_, drops, err := tr.RequestToCanonical([]byte(input))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	found := false
	for _, d := range drops {
		if d == "cached_content_unsupported" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected cached_content_unsupported drop, got: %v", drops)
	}
}

func TestRequestToCanonical_InvalidJSON(t *testing.T) {
	tr := translator()
	_, _, err := tr.RequestToCanonical([]byte(`{invalid json`))
	if err == nil {
		t.Error("expected error for invalid JSON, got nil")
	}
}
