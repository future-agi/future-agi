package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// testKeyAuth is a mock KeyAuthenticator for testing.
type testKeyAuth struct {
	keys map[string]struct {
		allowed []string
		denied  []string
	}
}

func (a *testKeyAuth) AuthenticateKey(rawKey string) (KeyGrant, bool) {
	entry, ok := a.keys[rawKey]
	if !ok {
		return KeyGrant{}, false
	}
	return KeyGrant{KeyID: "id-" + rawKey, AllowedTools: entry.allowed, DeniedTools: entry.denied}, true
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

	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer key-readonly"})

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

	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer key-safe"})

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

	requireUnauthorized(t, w, "invalid key")
	resp := decodeResponse(t, w)
	if resp.Error == nil {
		t.Fatal("expected error for invalid API key")
	}
	if resp.Error.Code != ErrCodeInvalidRequest {
		t.Fatalf("expected code %d, got %d", ErrCodeInvalidRequest, resp.Error.Code)
	}
	if s.SessionCount() != 0 {
		t.Fatalf("an invalid key opened %d session(s)", s.SessionCount())
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

	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer restricted"})

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
