package mcp

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"
)

func newTestServer(t *testing.T) *Server {
	t.Helper()
	return NewServer(ServerConfig{
		MaxAgentDepth:   10,
		ToolCallTimeout: 5 * time.Second,
		SessionTTL:      5 * time.Minute,
		Separator:       "_",
	})
}

func postMCP(t *testing.T, s *Server, msg *Message, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(msg)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("POST", "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	s.HandlePost(w, req)
	return w
}

func decodeResponse(t *testing.T, w *httptest.ResponseRecorder) *Message {
	t.Helper()
	var msg Message
	if err := json.NewDecoder(w.Body).Decode(&msg); err != nil {
		t.Fatalf("decode response: %v, body: %s", err, w.Body.String())
	}
	return &msg
}

func initializeSession(t *testing.T, s *Server) string {
	t.Helper()
	return initializeSessionWith(t, s, nil)
}

// initializeSessionWith opens a session sending the given headers, so a test
// with per-key auth set can open it with a key.
func initializeSessionWith(t *testing.T, s *Server, headers map[string]string) string {
	t.Helper()
	params := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		ClientInfo:      Implementation{Name: "test-agent", Version: "1.0"},
	}
	paramsData, _ := json.Marshal(params)
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodInitialize,
		Params:  paramsData,
	}
	w := postMCP(t, s, msg, headers)
	sessionID := w.Header().Get("MCP-Session-Id")
	if sessionID == "" {
		t.Fatalf("failed to initialize session: status %d, body %s", w.Code, w.Body.String())
	}
	return sessionID
}

// contains is a simple helper for string contains check.
func contains(s, substr string) bool {
	return len(s) >= len(substr) && searchSubstring(s, substr)
}

func searchSubstring(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
