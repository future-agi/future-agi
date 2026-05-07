package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
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

func TestServerToolsList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	// Register some tools directly in registry.
	s.Registry().RegisterServer("github", []Tool{
		{Name: "create_issue", Description: "Create an issue", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "list_repos", Description: "List repos", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	// First initialize to get session.
	sessionID := initializeSession(t, s)

	// List tools.
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`2`),
		Method:  MethodToolsList,
	}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(result.Tools))
	}
}

func TestServerToolsListNoSession(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodToolsList,
	}

	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing session")
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

func TestServerToolCallToolNotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "nonexistent_tool", Arguments: map[string]interface{}{"key": "value"}}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`3`),
		Method:  MethodToolsCall,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for non-existent tool")
	}
	if resp.Error.Code != ErrCodeMethodNotFound {
		t.Fatalf("expected code %d, got %d", ErrCodeMethodNotFound, resp.Error.Code)
	}
}

func TestServerDepthExceeded(t *testing.T) {
	s := NewServer(ServerConfig{
		MaxAgentDepth:   2,
		ToolCallTimeout: 5 * time.Second,
		SessionTTL:      5 * time.Minute,
	})
	defer s.Close()

	// Register a tool so we get past the "tool not found" check.
	s.Registry().RegisterServer("test", []Tool{
		{Name: "action", InputSchema: json.RawMessage(`{}`)},
	})

	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "test_action"}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`4`),
		Method:  MethodToolsCall,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		HeaderAgentDepth: "5",
	})

	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for depth exceeded")
	}
	if resp.Error.Code != ErrCodeInvalidRequest {
		t.Fatalf("expected code %d, got %d", ErrCodeInvalidRequest, resp.Error.Code)
	}
}

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

func TestServerToolCallWithMockUpstream(t *testing.T) {
	// Create a mock MCP server.
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var msg Message
		json.Unmarshal(body, &msg)

		switch msg.Method {
		case MethodInitialize:
			result := InitializeResult{
				ProtocolVersion: ProtocolVersion,
				Capabilities:    ServerCapabilities{Tools: &ToolsCapability{}},
				ServerInfo:      Implementation{Name: "mock", Version: "1.0"},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		case MethodToolsList:
			result := ListToolsResult{
				Tools: []Tool{
					{Name: "echo", Description: "Echo back", InputSchema: json.RawMessage(`{"type":"object"}`)},
				},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		case MethodToolsCall:
			var params ToolCallParams
			json.Unmarshal(msg.Params, &params)
			result := ToolCallResult{
				Content: []ContentPart{{Type: "text", Text: "echoed: " + params.Name}},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		default:
			resp, _ := NewResponse(msg.ID, struct{}{})
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)
		}
	}))
	defer mockServer.Close()

	// Create gateway server.
	s := newTestServer(t)
	defer s.Close()

	// Create client to mock upstream (URL points to mock/mcp, but mock handles at root).
	// The HTTPTransport appends /mcp to the base URL, so use mock base.
	client := NewClient(ClientConfig{
		ServerID:      "mock",
		URL:           mockServer.URL, // HTTPTransport appends /mcp
		TransportType: "http",
	})

	// The mock handles all paths, so we need the transport to hit the right URL.
	// Override: create transport directly for the test.
	transport := NewHTTPTransport(mockServer.URL, AuthConfig{})
	// Monkey-patch: the base URL already has /mcp suffix from transport, but mock serves at /.
	// For the test, let's just set the base URL without /mcp suffix.
	client.transport = transport
	client.healthy.Store(true)

	// Manually discover tools.
	toolsList := []Tool{
		{Name: "echo", Description: "Echo back", InputSchema: json.RawMessage(`{"type":"object"}`)},
	}
	client.tools.Store(&toolsList)

	s.RegisterClient(client)

	// Verify tools registered.
	if s.Registry().ToolCount() != 1 {
		t.Fatalf("expected 1 tool, got %d", s.Registry().ToolCount())
	}

	// Initialize session and call tool.
	sessionID := initializeSession(t, s)

	params := ToolCallParams{Name: "mock_echo", Arguments: map[string]interface{}{"input": "hello"}}
	paramsData, _ := json.Marshal(params)

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`10`),
		Method:  MethodToolsCall,
		Params:  paramsData,
	}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ToolCallResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Content) != 1 {
		t.Fatalf("expected 1 content part, got %d", len(result.Content))
	}
	if result.Content[0].Text != "echoed: echo" {
		t.Fatalf("expected 'echoed: echo', got %s", result.Content[0].Text)
	}
}

// --- Helpers ---

func initializeSession(t *testing.T, s *Server) string {
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
	w := postMCP(t, s, msg, nil)
	sessionID := w.Header().Get("MCP-Session-Id")
	if sessionID == "" {
		t.Fatal("failed to initialize session")
	}
	return sessionID
}

// --- Guard Integration Tests ---

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

// ============================================================
// 14.7.2: Per-Key MCP Tool Filtering
// ============================================================

// testKeyAuth is a mock KeyAuthenticator for testing.
type testKeyAuth struct {
	keys map[string]struct {
		allowed []string
		denied  []string
	}
}

func (a *testKeyAuth) AuthenticateKey(rawKey string) (allowed []string, denied []string, valid bool) {
	entry, ok := a.keys[rawKey]
	if !ok {
		return nil, nil, false
	}
	return entry.allowed, entry.denied, true
}

func TestPerKeyToolFiltering_AllowedList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterServer("github", []Tool{
		{Name: "create_issue", Description: "Create an issue", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "list_repos", Description: "List repos", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "delete_repo", Description: "Delete repo", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"key-readonly": {allowed: []string{"github_list_repos"}, denied: nil},
		},
	})

	sessionID := initializeSession(t, s)

	// List tools with the readonly key — should only see list_repos.
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		"Authorization":  "Bearer key-readonly",
	})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Tools) != 1 {
		t.Fatalf("expected 1 tool for readonly key, got %d", len(result.Tools))
	}
	if result.Tools[0].Name != "github_list_repos" {
		t.Fatalf("expected github_list_repos, got %s", result.Tools[0].Name)
	}
}

func TestPerKeyToolFiltering_DeniedList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterServer("github", []Tool{
		{Name: "create_issue", Description: "Create", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "delete_repo", Description: "Delete", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"key-safe": {allowed: nil, denied: []string{"github_delete_repo"}},
		},
	})

	sessionID := initializeSession(t, s)

	// List tools with denied key — should not see delete_repo.
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		"Authorization":  "Bearer key-safe",
	})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Tools) != 1 {
		t.Fatalf("expected 1 tool (delete_repo denied), got %d", len(result.Tools))
	}
	if result.Tools[0].Name != "github_create_issue" {
		t.Fatalf("expected github_create_issue, got %s", result.Tools[0].Name)
	}
}

func TestPerKeyToolFiltering_InvalidKeyRejected(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"valid-key": {allowed: nil, denied: nil},
		},
	})

	// Send with invalid key.
	params := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		ClientInfo:      Implementation{Name: "test", Version: "1.0"},
	}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodInitialize, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{
		"Authorization": "Bearer bad-key",
	})

	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for invalid API key")
	}
	if resp.Error.Code != ErrCodeInvalidRequest {
		t.Fatalf("expected code %d, got %d", ErrCodeInvalidRequest, resp.Error.Code)
	}
}

func TestPerKeyToolFiltering_NoKeyPassesThrough(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterServer("gh", []Tool{
		{Name: "t1", InputSchema: json.RawMessage(`{}`)},
		{Name: "t2", InputSchema: json.RawMessage(`{}`)},
	})

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"my-key": {allowed: []string{"gh_t1"}, denied: nil},
		},
	})

	sessionID := initializeSession(t, s)

	// List tools WITHOUT an Authorization header — should see all tools (no filtering).
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Tools) != 2 {
		t.Fatalf("expected 2 tools with no auth header, got %d", len(result.Tools))
	}
}

func TestPerKeyToolFiltering_ToolCallBlocked(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "secret_tool", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("srv", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "secret data"}}}
		resp, _ := NewResponse(msg.ID, result)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "srv", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["srv"] = client
	s.clientsMu.Unlock()

	s.SetKeyAuth(&testKeyAuth{
		keys: map[string]struct {
			allowed []string
			denied  []string
		}{
			"restricted": {allowed: nil, denied: []string{"srv_secret_tool"}},
		},
	})

	sessionID := initializeSession(t, s)

	// Try calling the denied tool.
	params := ToolCallParams{Name: "srv_secret_tool", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{
		"MCP-Session-Id": sessionID,
		"Authorization":  "Bearer restricted",
	})

	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for denied tool call")
	}
}

// ============================================================
// 14.7.3: Tool Input Schema Validation
// ============================================================

func TestSchemaValidation_MissingRequired(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	schema := `{"type":"object","required":["name","age"],"properties":{"name":{"type":"string"},"age":{"type":"integer"}}}`
	tools := []Tool{{Name: "action", InputSchema: json.RawMessage(schema)}}
	s.Registry().RegisterServer("srv", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		resp, _ := NewResponse(msg.ID, ToolCallResult{Content: []ContentPart{{Type: "text", Text: "ok"}}})
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "srv", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["srv"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	// Missing "age" field.
	params := ToolCallParams{Name: "srv_action", Arguments: map[string]interface{}{"name": "Alice"}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing required field 'age'")
	}
	if resp.Error.Code != ErrCodeInvalidParams {
		t.Fatalf("expected code %d, got %d", ErrCodeInvalidParams, resp.Error.Code)
	}
}

func TestSchemaValidation_WrongType(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	schema := `{"type":"object","properties":{"count":{"type":"integer"}}}`
	tools := []Tool{{Name: "action", InputSchema: json.RawMessage(schema)}}
	s.Registry().RegisterServer("srv", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		resp, _ := NewResponse(msg.ID, ToolCallResult{Content: []ContentPart{{Type: "text", Text: "ok"}}})
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "srv", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["srv"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	// "count" should be integer, passing string.
	params := ToolCallParams{Name: "srv_action", Arguments: map[string]interface{}{"count": "not-a-number"}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for wrong type (string instead of integer)")
	}
	if resp.Error.Code != ErrCodeInvalidParams {
		t.Fatalf("expected code %d, got %d", ErrCodeInvalidParams, resp.Error.Code)
	}
}

func TestSchemaValidation_ValidArgs(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	schema := `{"type":"object","required":["name"],"properties":{"name":{"type":"string"},"count":{"type":"number"}}}`
	tools := []Tool{{Name: "action", InputSchema: json.RawMessage(schema)}}
	s.Registry().RegisterServer("srv", tools)

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		resp, _ := NewResponse(msg.ID, ToolCallResult{Content: []ContentPart{{Type: "text", Text: "ok"}}})
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "srv", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	client.tools.Store(&tools)
	s.clientsMu.Lock()
	s.clients["srv"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	// Valid args.
	params := ToolCallParams{Name: "srv_action", Arguments: map[string]interface{}{"name": "Alice", "count": 42.0}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("expected success for valid args, got: %s", resp.Error.Message)
	}
}

func TestValidateArgsAgainstSchema_Unit(t *testing.T) {
	schema := json.RawMessage(`{"type":"object","required":["q"],"properties":{"q":{"type":"string"},"limit":{"type":"integer"}}}`)

	// Missing required.
	if msg := validateArgsAgainstSchema(schema, map[string]interface{}{"limit": 10.0}); msg == "" {
		t.Error("expected error for missing 'q'")
	}

	// All valid.
	if msg := validateArgsAgainstSchema(schema, map[string]interface{}{"q": "hello", "limit": 5.0}); msg != "" {
		t.Errorf("expected pass, got: %s", msg)
	}

	// Wrong type for 'limit' (float with decimal is not integer).
	if msg := validateArgsAgainstSchema(schema, map[string]interface{}{"q": "hello", "limit": 5.5}); msg == "" {
		t.Error("expected error for float limit (not integer)")
	}

	// Integer as whole float should pass.
	if msg := validateArgsAgainstSchema(schema, map[string]interface{}{"q": "hello", "limit": 5.0}); msg != "" {
		t.Errorf("expected pass for 5.0 as integer, got: %s", msg)
	}
}

func TestMatchesJSONType(t *testing.T) {
	tests := []struct {
		val      interface{}
		jsonType string
		expected bool
	}{
		{"hello", "string", true},
		{42.0, "string", false},
		{42.0, "number", true},
		{42.0, "integer", true},  // 42.0 == int64(42)
		{42.5, "integer", false}, // 42.5 != int64(42)
		{true, "boolean", true},
		{true, "string", false},
		{[]interface{}{1, 2}, "array", true},
		{map[string]interface{}{"a": 1}, "object", true},
		{nil, "null", true},
		{"test", "unknown_type", true}, // Unknown types pass
	}

	for _, tt := range tests {
		got := matchesJSONType(tt.val, tt.jsonType)
		if got != tt.expected {
			t.Errorf("matchesJSONType(%v, %q) = %v, want %v", tt.val, tt.jsonType, got, tt.expected)
		}
	}
}

// ============================================================
// 14.7.5: Tool Execution Playground
// ============================================================

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

// ============================================================
// 14.7.6: MCP Resources & Prompts
// ============================================================

func TestResourcesList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	// Register resources.
	s.Registry().RegisterResources("files", []Resource{
		{URI: "file:///readme.md", Name: "README", MIMEType: "text/markdown"},
		{URI: "file:///config.json", Name: "Config", MIMEType: "application/json"},
	})

	sessionID := initializeSession(t, s)

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListResourcesResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Resources) != 2 {
		t.Fatalf("expected 2 resources, got %d", len(result.Resources))
	}
}

func TestResourcesListEmpty(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListResourcesResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Resources) != 0 {
		t.Fatalf("expected 0 resources, got %d", len(result.Resources))
	}
}

func TestResourcesRead(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterResources("files", []Resource{
		{URI: "file:///readme.md", Name: "README"},
	})

	// Mock upstream that responds to resources/read.
	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)

		switch msg.Method {
		case MethodResourcesRead:
			result := ReadResourceResult{
				Contents: []ResourceContent{{URI: "file:///readme.md", Text: "# Hello"}},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)
		default:
			resp, _ := NewResponse(msg.ID, struct{}{})
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)
		}
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "files", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	s.clientsMu.Lock()
	s.clients["files"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	params := ReadResourceParams{URI: "file:///readme.md"}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesRead, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ReadResourceResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Contents) != 1 {
		t.Fatalf("expected 1 content, got %d", len(result.Contents))
	}
	if result.Contents[0].Text != "# Hello" {
		t.Fatalf("expected '# Hello', got %s", result.Contents[0].Text)
	}
}

func TestResourcesReadNotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	params := ReadResourceParams{URI: "file:///nonexistent"}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesRead, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for non-existent resource")
	}
	if resp.Error.Code != ErrCodeMethodNotFound {
		t.Fatalf("expected code %d, got %d", ErrCodeMethodNotFound, resp.Error.Code)
	}
}

func TestPromptsList(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterPrompts("ai", []Prompt{
		{Name: "summarize", Description: "Summarize text", Arguments: []PromptArgument{{Name: "text", Required: true}}},
		{Name: "translate", Description: "Translate text"},
	})

	sessionID := initializeSession(t, s)

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})

	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ListPromptsResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Prompts) != 2 {
		t.Fatalf("expected 2 prompts, got %d", len(result.Prompts))
	}
}

func TestPromptsGet(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	s.Registry().RegisterPrompts("ai", []Prompt{
		{Name: "summarize", Description: "Summarize text"},
	})

	// Mock upstream that responds to prompts/get.
	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)

		switch msg.Method {
		case MethodPromptsGet:
			result := GetPromptResult{
				Description: "Summary prompt",
				Messages:    []PromptMessage{{Role: "user", Content: ContentPart{Type: "text", Text: "Summarize: {text}"}}},
			}
			resp, _ := NewResponse(msg.ID, result)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)
		default:
			resp, _ := NewResponse(msg.ID, struct{}{})
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)
		}
	}))
	defer mockUpstream.Close()

	client := NewClient(ClientConfig{ServerID: "ai", URL: mockUpstream.URL, TransportType: "http"})
	client.transport = NewHTTPTransport(mockUpstream.URL, AuthConfig{})
	client.healthy.Store(true)
	s.clientsMu.Lock()
	s.clients["ai"] = client
	s.clientsMu.Unlock()

	sessionID := initializeSession(t, s)

	params := GetPromptParams{Name: "ai_summarize", Arguments: map[string]string{"text": "hello world"}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsGet, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result GetPromptResult
	json.Unmarshal(resp.Result, &result)
	if len(result.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(result.Messages))
	}
}

func TestPromptsGetNotFound(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	sessionID := initializeSession(t, s)

	params := GetPromptParams{Name: "nonexistent_prompt"}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsGet, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for non-existent prompt")
	}
}

func TestResourcesRequireSession(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodResourcesList}
	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing session on resources/list")
	}
}

func TestPromptsRequireSession(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPromptsList}
	w := postMCP(t, s, msg, nil)
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing session on prompts/list")
	}
}

// ============================================================
// 14.7.7: Tool Versioning & Deprecation
// ============================================================

func TestToolCallDeprecationWarning(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "old_api", Description: "Old API", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	// Set version info marking the tool as deprecated.
	s.Registry().SetToolVersions(map[string]ToolVersionInfo{
		"mock_old_api": {
			Version:            "1.0",
			Deprecated:         true,
			DeprecationMessage: "old_api is deprecated",
			ReplacedBy:         "mock_new_api",
		},
	})

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "result"}}}
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

	params := ToolCallParams{Name: "mock_old_api", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("deprecated tool should still work, got error: %s", resp.Error.Message)
	}

	var result ToolCallResult
	json.Unmarshal(resp.Result, &result)

	// Should have the original content plus a deprecation warning.
	if len(result.Content) < 2 {
		t.Fatalf("expected at least 2 content parts (result + deprecation), got %d", len(result.Content))
	}

	lastPart := result.Content[len(result.Content)-1]
	if lastPart.Type != "text" {
		t.Fatalf("expected text type for deprecation warning, got %s", lastPart.Type)
	}
	if !contains(lastPart.Text, "DEPRECATED") {
		t.Fatalf("expected deprecation warning, got: %s", lastPart.Text)
	}
	if !contains(lastPart.Text, "mock_new_api") {
		t.Fatalf("expected replacement tool in warning, got: %s", lastPart.Text)
	}
}

func TestToolCallNoDeprecationWarningForNonDeprecated(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	tools := []Tool{{Name: "active_api", Description: "Active API", InputSchema: json.RawMessage(`{}`)}}
	s.Registry().RegisterServer("mock", tools)

	// Not deprecated.
	s.Registry().SetToolVersions(map[string]ToolVersionInfo{
		"mock_active_api": {Version: "2.0", Deprecated: false},
	})

	mockUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg Message
		json.NewDecoder(r.Body).Decode(&msg)
		result := ToolCallResult{Content: []ContentPart{{Type: "text", Text: "result"}}}
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

	params := ToolCallParams{Name: "mock_active_api", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	var result ToolCallResult
	json.Unmarshal(resp.Result, &result)

	// Should have exactly 1 content part (no deprecation warning).
	if len(result.Content) != 1 {
		t.Fatalf("expected 1 content part (no deprecation), got %d", len(result.Content))
	}
}

// ============================================================
// Helper: extractBearerToken unit tests
// ============================================================

func TestExtractBearerToken(t *testing.T) {
	tests := []struct {
		auth     string
		expected string
	}{
		{"Bearer sk-test-123", "sk-test-123"},
		{"bearer sk-test-123", "sk-test-123"},
		{"BEARER sk-test-123", "sk-test-123"},
		{"Bearer  sk-with-spaces  ", "sk-with-spaces"},
		{"Token sk-test-123", ""},
		{"", ""},
		{"Bearer", ""},
	}

	for _, tt := range tests {
		req := httptest.NewRequest("GET", "/", nil)
		if tt.auth != "" {
			req.Header.Set("Authorization", tt.auth)
		}
		got := extractBearerToken(req)
		if got != tt.expected {
			t.Errorf("extractBearerToken(auth=%q) = %q, want %q", tt.auth, got, tt.expected)
		}
	}
}

// ============================================================
// Helper: filterToolsByKey / checkToolKeyAccess unit tests
// ============================================================

func TestFilterToolsByKey(t *testing.T) {
	tools := []Tool{
		{Name: "a_t1"}, {Name: "a_t2"}, {Name: "b_t3"},
	}

	// No filtering.
	ctx := context.Background()
	filtered := filterToolsByKey(ctx, tools)
	if len(filtered) != 3 {
		t.Fatalf("expected 3 tools with no context, got %d", len(filtered))
	}

	// Allowed list.
	ctx = context.WithValue(context.Background(), ctxKeyAllowedTools, []string{"a_t1", "b_t3"})
	filtered = filterToolsByKey(ctx, tools)
	if len(filtered) != 2 {
		t.Fatalf("expected 2 tools with allowed list, got %d", len(filtered))
	}

	// Denied list.
	ctx = context.WithValue(context.Background(), ctxKeyDeniedTools, []string{"a_t2"})
	filtered = filterToolsByKey(ctx, tools)
	if len(filtered) != 2 {
		t.Fatalf("expected 2 tools with denied list, got %d", len(filtered))
	}
}

func TestCheckToolKeyAccess(t *testing.T) {
	// No restrictions.
	ctx := context.Background()
	if msg := checkToolKeyAccess(ctx, "any_tool"); msg != "" {
		t.Errorf("expected empty, got: %s", msg)
	}

	// Allowed list — tool in list.
	ctx = context.WithValue(context.Background(), ctxKeyAllowedTools, []string{"a_tool"})
	if msg := checkToolKeyAccess(ctx, "a_tool"); msg != "" {
		t.Errorf("expected empty for allowed tool, got: %s", msg)
	}

	// Allowed list — tool NOT in list.
	if msg := checkToolKeyAccess(ctx, "other_tool"); msg == "" {
		t.Error("expected error for tool not in allowed list")
	}

	// Denied list — tool in list.
	ctx = context.WithValue(context.Background(), ctxKeyDeniedTools, []string{"bad_tool"})
	if msg := checkToolKeyAccess(ctx, "bad_tool"); msg == "" {
		t.Error("expected error for denied tool")
	}

	// Denied list — tool NOT in list.
	if msg := checkToolKeyAccess(ctx, "good_tool"); msg != "" {
		t.Errorf("expected empty for non-denied tool, got: %s", msg)
	}
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
