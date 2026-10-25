package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// newKeyedTestServer is a server with per-key auth set, as the gateway sets it
// whenever request auth is on, and two tools so filtering is visible.
func newKeyedTestServer(t *testing.T) *Server {
	t.Helper()
	s := newTestServer(t)
	t.Cleanup(s.Close)
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
	return s
}

// requireUnauthorized checks for the MCP authorization answer to a request
// without a valid key: HTTP 401 with a Bearer challenge, and no session.
func requireUnauthorized(t *testing.T, w *httptest.ResponseRecorder, what string) {
	t.Helper()
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("%s: status %d, want 401. Body: %s", what, w.Code, w.Body.String())
	}
	if got := w.Header().Get("WWW-Authenticate"); !strings.HasPrefix(got, "Bearer ") {
		t.Fatalf("%s: WWW-Authenticate %q, want a Bearer challenge", what, got)
	}
	if id := w.Header().Get("MCP-Session-Id"); id != "" {
		t.Fatalf("%s: answered with session %q", what, id)
	}
}

// With per-key auth set, every JSON-RPC method needs a valid key: a request
// without one gets 401 with a Bearer challenge and opens no session.
func TestPerKeyToolFiltering_NoKeyIsRefused(t *testing.T) {
	s := newKeyedTestServer(t)
	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer my-key"})

	initParams, _ := json.Marshal(InitializeParams{
		ProtocolVersion: ProtocolVersion,
		ClientInfo:      Implementation{Name: "anon", Version: "1.0"},
	})
	callParams, _ := json.Marshal(ToolCallParams{Name: "gh_t2", Arguments: map[string]interface{}{}})
	for _, msg := range []*Message{
		{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodInitialize, Params: initParams},
		{JSONRPC: "2.0", Method: MethodInitialized},
		{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList},
		{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: callParams},
		{JSONRPC: "2.0", ID: json.RawMessage(`4`), Method: MethodResourcesList},
		{JSONRPC: "2.0", ID: json.RawMessage(`5`), Method: MethodPromptsList},
		{JSONRPC: "2.0", ID: json.RawMessage(`6`), Method: MethodPing},
		{JSONRPC: "2.0", Method: MethodCancelled},
	} {
		w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID})
		requireUnauthorized(t, w, "POST /mcp "+msg.Method+" with no key")
	}
	if n := s.SessionCount(); n != 1 {
		t.Fatalf("requests with no key changed the session count to %d, want 1", n)
	}

	// Credentials that are not a key the gateway reads are no key at all.
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`7`), Method: MethodToolsList}
	for _, header := range []map[string]string{
		{"Authorization": "Basic bXkta2V5Og=="},
		{"Authorization": "Bearer "},
		{"x-api-key": ""},
	} {
		header["MCP-Session-Id"] = sessionID
		requireUnauthorized(t, postMCP(t, s, msg, header), fmt.Sprintf("tools/list with %v", header))
	}

	// A body that is not even JSON-RPC is refused before it is parsed.
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader("not json"))
	w := httptest.NewRecorder()
	s.HandlePost(w, req)
	requireUnauthorized(t, w, "POST /mcp with no key and a malformed body")
}

// The gateway accepts a key as a bearer token or in x-api-key, and the MCP
// server applies that key's tool allow-list whichever header carries it.
func TestPerKeyToolFiltering_XAPIKeyIsFiltered(t *testing.T) {
	s := newKeyedTestServer(t)
	sessionID := initializeSessionWith(t, s, map[string]string{"x-api-key": "my-key"})

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	w := postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID, "x-api-key": "my-key"})
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}
	var result ListToolsResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatal(err)
	}
	if len(result.Tools) != 1 || result.Tools[0].Name != "gh_t1" {
		t.Fatalf("x-api-key restricted to gh_t1 listed %v", result.Tools)
	}

	callParams, _ := json.Marshal(ToolCallParams{Name: "gh_t2", Arguments: map[string]interface{}{}})
	call := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: callParams}
	resp = decodeResponse(t, postMCP(t, s, call, map[string]string{"MCP-Session-Id": sessionID, "x-api-key": "my-key"}))
	if resp.Error == nil || !strings.Contains(resp.Error.Message, "not in allowed list") {
		t.Fatalf("x-api-key restricted to gh_t1 called gh_t2: %+v", resp)
	}
}

// GET /mcp opens the server-to-client stream. With per-key auth set it needs
// a valid key as well as a session id.
func TestSSERequiresAKeyWhenKeyAuthIsSet(t *testing.T) {
	s := newKeyedTestServer(t)
	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer my-key"})

	openStream := func(headers map[string]string) *httptest.ResponseRecorder {
		ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
		defer cancel()
		req := httptest.NewRequest("GET", "/mcp", nil).WithContext(ctx)
		req.Header.Set("MCP-Session-Id", sessionID)
		for k, v := range headers {
			req.Header.Set(k, v)
		}
		w := httptest.NewRecorder()
		s.HandleSSE(w, req)
		return w
	}

	requireUnauthorized(t, openStream(nil), "GET /mcp with no key")
	requireUnauthorized(t, openStream(map[string]string{"Authorization": "Bearer wrong"}), "GET /mcp with a wrong key")

	w := openStream(map[string]string{"Authorization": "Bearer my-key"})
	if w.Code != http.StatusOK || w.Header().Get("Content-Type") != "text/event-stream" {
		t.Fatalf("GET /mcp with a valid key: status %d, content type %q", w.Code, w.Header().Get("Content-Type"))
	}
}

// Without per-key auth (gateway request auth off) the endpoint stays open.
func TestNoKeyAuthServesWithoutAKey(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	s.Registry().RegisterServer("gh", []Tool{{Name: "t1", InputSchema: json.RawMessage(`{}`)}})
	sessionID := initializeSession(t, s)
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
	resp := decodeResponse(t, postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID}))
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}
}
