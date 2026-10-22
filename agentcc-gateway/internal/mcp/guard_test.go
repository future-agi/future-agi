package mcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// testGuard is a simple ToolCallGuard for testing.
type testGuard struct {
	blockedTools   map[string]bool
	blockedServers map[string]bool
	blockOutput    bool
}

func (g *testGuard) CheckPre(toolName, serverID string, arguments map[string]interface{}) string {
	if g.blockedTools[toolName] {
		return "tool blocked: " + toolName
	}
	if g.blockedServers[serverID] {
		return "server blocked: " + serverID
	}
	return ""
}

func (g *testGuard) CheckPost(toolName, serverID string, result *ToolCallResult) string {
	if g.blockOutput {
		return "output blocked"
	}
	return ""
}

func TestToolCallBlockedByGuardPre(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	// Set guard that blocks "mock_dangerous".
	s.SetGuard(&testGuard{blockedTools: map[string]bool{"mock_dangerous": true}})

	// Register a tool named "dangerous" on server "mock" → namespaced as "mock_dangerous".
	tools := []Tool{
		{Name: "dangerous", Description: "Dangerous tool"},
		{Name: "safe", Description: "Safe tool"},
	}
	s.registry.RegisterServer("mock", tools)

	// Create a mock client.
	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "should not reach here"}}}
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

	sessionID := initializeSession(t, s)

	// Try calling the blocked tool.
	params := ToolCallParams{Name: "mock_dangerous", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for blocked tool")
	}
	if resp.Error.Code != ErrCodeInvalidRequest {
		t.Fatalf("expected invalid request error, got %d", resp.Error.Code)
	}

	// Try calling the safe tool — should succeed.
	params2 := ToolCallParams{Name: "mock_safe", Arguments: map[string]interface{}{}}
	paramsData2, _ := json.Marshal(params2)
	msg2 := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsCall, Params: paramsData2}

	w2 := postMCP(t, s, msg2, map[string]string{"MCP-Session-Id": sessionID})
	resp2 := decodeResponse(t, w2)
	if resp2.Error != nil {
		t.Fatalf("safe tool should not be blocked: %s", resp2.Error.Message)
	}
}

func TestToolCallBlockedByGuardPost(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	// Set guard that blocks all outputs.
	s.SetGuard(&testGuard{blockOutput: true})

	// Register tool and mock upstream.
	tools := []Tool{{Name: "echo", Description: "Echo"}}
	s.registry.RegisterServer("mock", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "response"}}}
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

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "mock_echo", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for blocked output")
	}
}

func TestToolCallNoGuardPassesThrough(t *testing.T) {
	// Without a guard set, tools should work normally.
	s := newTestServer(t)
	defer s.Close()

	// No guard set — s.guard is nil.
	tools := []Tool{{Name: "echo", Description: "Echo"}}
	s.registry.RegisterServer("mock", tools)

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

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "mock_echo", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("expected success with no guard, got: %s", resp.Error.Message)
	}
}
