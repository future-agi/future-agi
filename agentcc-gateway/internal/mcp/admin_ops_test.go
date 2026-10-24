package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestServerSessionCount(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	if s.SessionCount() != 0 {
		t.Fatal("expected 0 sessions")
	}

	initializeSession(t, s)
	initializeSession(t, s)

	if s.SessionCount() != 2 {
		t.Fatalf("expected 2 sessions, got %d", s.SessionCount())
	}
}

func TestServerClientStatuses(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	statuses := s.ClientStatuses()
	if len(statuses) != 0 {
		t.Fatal("expected 0 client statuses")
	}
}

func TestTestTool_Success(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "echo", Description: "Echo tool", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "echoed"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	ctx := context.Background()
	result := s.TestTool(ctx, "mock_echo", map[string]interface{}{"input": "hello"})

	if result.Error != "" {
		t.Fatalf("expected no error, got: %s", result.Error)
	}
	if result.IsError {
		t.Fatal("expected IsError=false")
	}
	if len(result.Content) != 1 || result.Content[0].Text != "echoed" {
		t.Fatalf("unexpected content: %+v", result.Content)
	}
	if result.DurationMs <= 0 {
		t.Fatal("expected positive duration")
	}
	if result.Server != "mock" {
		t.Fatalf("expected server 'mock', got %s", result.Server)
	}
	if result.GuardrailPre != "skipped" || result.GuardrailPost != "skipped" {
		t.Fatalf("expected guardrails skipped (no guard set), got pre=%s post=%s", result.GuardrailPre, result.GuardrailPost)
	}
}

func TestTestTool_NotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	result := s.TestTool(context.Background(), "nonexistent_tool", nil)
	if result.Error == "" {
		t.Fatal("expected error for non-existent tool")
	}
}

func TestTestTool_GuardrailBlocks(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetGuard(&testGuard{blockedTools: map[string]bool{"mock_dangerous": true}})

	tools := []Tool{{Name: "dangerous", Description: "Dangerous"}}
	s.Registry().RegisterServer("mock", tools)

	// No upstream needed — guardrail blocks before reaching upstream.

	result := s.TestTool(context.Background(), "mock_dangerous", map[string]interface{}{})
	if result.Error == "" {
		t.Fatal("expected error from guardrail block")
	}
	if result.GuardrailPre != "blocked" {
		t.Fatalf("expected guardrail_pre='blocked', got %s", result.GuardrailPre)
	}
}

func TestTestTool_GuardrailPasses(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetGuard(&testGuard{}) // Guard that blocks nothing

	tools := []Tool{{Name: "safe", Description: "Safe tool", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "ok"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	result := s.TestTool(context.Background(), "mock_safe", map[string]interface{}{})
	if result.Error != "" {
		t.Fatalf("expected success, got: %s", result.Error)
	}
	if result.GuardrailPre != "pass" {
		t.Fatalf("expected guardrail_pre='pass', got %s", result.GuardrailPre)
	}
	if result.GuardrailPost != "pass" {
		t.Fatalf("expected guardrail_post='pass', got %s", result.GuardrailPost)
	}
}

func TestTestTool_GuardrailBlocksOutput(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetGuard(&testGuard{blockOutput: true})

	tools := []Tool{{Name: "echo", Description: "Echo", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "bad content"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "mock", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["mock"] = client
	s.clientsMu.Unlock()

	result := s.TestTool(context.Background(), "mock_echo", map[string]interface{}{})
	if result.Error == "" {
		t.Fatal("expected error from post-guardrail block")
	}
	if result.GuardrailPost != "blocked" {
		t.Fatalf("expected guardrail_post='blocked', got %s", result.GuardrailPost)
	}
}
