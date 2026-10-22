package mcp

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

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
