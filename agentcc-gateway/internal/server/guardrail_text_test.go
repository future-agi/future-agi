package server

import (
	"encoding/json"
	"testing"
)

func TestGuardrailRequest_StrictTranslationStillUsed(t *testing.T) {
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 64,
		"messages": [{"role": "user", "content": [{"type": "text", "text": "Hello there"}]}]
	}`)

	req := guardrailRequest("anthropic", body)
	if req == nil {
		t.Fatal("expected a canonical request for a translatable body")
	}
	if len(req.Messages) != 1 || req.Messages[0].Role != "user" {
		t.Fatalf("unexpected messages: %+v", req.Messages)
	}
}

func TestGuardrailRequest_UntranslatableBlocksFallBackToText(t *testing.T) {
	// The strict translator refuses this body (container_upload cannot be
	// translated), but the native route still forwards it. Guardrails must
	// still see the readable text instead of losing the request entirely.
	body := []byte(`{
		"model": "claude-3-5-sonnet-20241022",
		"max_tokens": 64,
		"system": "You are a helpful assistant.",
		"messages": [
			{"role": "user", "content": [
				{"type": "text", "text": "Read this file"},
				{"type": "container_upload", "file_id": "file-abc"}
			]}
		]
	}`)

	req := guardrailRequest("anthropic", body)
	if req == nil {
		t.Fatal("expected the lenient fallback, got nil")
	}
	if req.Model != "claude-3-5-sonnet-20241022" {
		t.Errorf("Model = %q, want the body's model", req.Model)
	}
	if len(req.Messages) != 2 {
		t.Fatalf("messages = %+v, want system + user", req.Messages)
	}
	if req.Messages[0].Role != "system" {
		t.Errorf("messages[0].Role = %q, want system", req.Messages[0].Role)
	}
	var sysText, userText string
	if err := json.Unmarshal(req.Messages[0].Content, &sysText); err != nil || sysText != "You are a helpful assistant." {
		t.Errorf("system text = %q (err %v), want the system field", sysText, err)
	}
	if err := json.Unmarshal(req.Messages[1].Content, &userText); err != nil || userText != "Read this file" {
		t.Errorf("user text = %q (err %v), want the text block's text", userText, err)
	}
}

func TestGuardrailRequest_UnreadableBodyStillNil(t *testing.T) {
	if req := guardrailRequest("anthropic", []byte(`{not json`)); req != nil {
		t.Fatalf("expected nil for an unreadable body, got %+v", req)
	}
}
