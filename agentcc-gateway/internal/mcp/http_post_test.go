package mcp

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestServerInitialize(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	params := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		ClientInfo:      Implementation{Name: "test-agent", Version: "1.0"},
		Capabilities:    ClientCapabilities{},
	}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodInitialize,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	// Should have session ID in header.
	sessionID := w.Header().Get("MCP-Session-Id")
	if sessionID == "" {
		t.Fatal("expected MCP-Session-Id header")
	}

	// Parse result.
	var result InitializeResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if result.ProtocolVersion != ProtocolVersion {
		t.Fatalf("expected protocol %s, got %s", ProtocolVersion, result.ProtocolVersion)
	}
	if result.ServerInfo.Name != "agentcc-gateway" {
		t.Fatalf("expected server name 'agentcc-gateway', got %s", result.ServerInfo.Name)
	}
	if result.Capabilities.Tools == nil {
		t.Fatal("expected tools capability")
	}
}

func TestServerPing(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodPing,
	}

	w := postMCP(t, s, msg, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}
}

func TestServerUnknownMethod(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  "unknown/method",
	}

	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for unknown method")
	}
	if resp.Error.Code != ErrCodeMethodNotFound {
		t.Fatalf("expected code %d, got %d", ErrCodeMethodNotFound, resp.Error.Code)
	}
}

func TestServerInvalidJSON(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader([]byte("not json")))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.HandlePost(w, req)

	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected parse error")
	}
	if resp.Error.Code != ErrCodeParse {
		t.Fatalf("expected code %d, got %d", ErrCodeParse, resp.Error.Code)
	}
}

func TestServerNotifications(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		Method:  MethodInitialized,
	}

	w := postMCP(t, s, msg, nil)
	if w.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", w.Code)
	}
}

// requestAPIKey must read a key exactly as middleware.KeyAuth does, since the
// gateway authenticates that key before the MCP server filters tools by it.
func TestRequestAPIKey(t *testing.T) {
	tests := []struct {
		auth, apiKey string
		expected     string
	}{
		{"Bearer sk-test-123", "", "sk-test-123"},
		{"Bearer sk-bearer", "sk-header", "sk-bearer"},
		{"", "sk-header", "sk-header"},
		{"Basic dXNlcjpwYXNz", "sk-header", "sk-header"},
		{"bearer sk-test-123", "", ""}, // KeyAuth's prefix match is case-sensitive
		{"Token sk-test-123", "", ""},
		{"Bearer ", "sk-header", ""},
		{"Bearer", "", ""},
		{"", "", ""},
	}

	for _, tt := range tests {
		req := httptest.NewRequest("GET", "/", nil)
		if tt.auth != "" {
			req.Header.Set("Authorization", tt.auth)
		}
		if tt.apiKey != "" {
			req.Header.Set("x-api-key", tt.apiKey)
		}
		if got := requestAPIKey(req); got != tt.expected {
			t.Errorf("requestAPIKey(auth=%q, x-api-key=%q) = %q, want %q", tt.auth, tt.apiKey, got, tt.expected)
		}
	}
}
