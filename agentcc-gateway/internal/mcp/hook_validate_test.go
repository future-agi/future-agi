package mcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

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

func TestSchemaValidation_EmptyArgumentsStillChecksRequired(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()

	schema := `{"type":"object","required":["name"],"properties":{"name":{"type":"string"}}}`
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

	// Empty arguments, but "name" is required.
	params := ToolCallParams{Name: "srv_action", Arguments: map[string]interface{}{}}
	paramsData, _ := json.Marshal(params)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: paramsData}

	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for missing required field with empty arguments")
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
