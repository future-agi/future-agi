package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// An MCP session belongs to the API key that opened it (initialize). Every
// session-scoped call, including GET /mcp, which attaches the session's
// notification stream, answers a session id opened by any other key, even one
// in the same org, exactly as it answers an unknown session id: 404. The
// session itself is left alone, so its owner keeps using it.

const (
	mcpOrgA     = "org-mcp-a"
	mcpOrgB     = "org-mcp-b"
	mcpKeyA     = "sk-agentcc-mcp-org-a"
	mcpKeyA2    = "sk-agentcc-mcp-org-a-second"
	mcpKeyB     = "sk-agentcc-mcp-org-b"
	mcpKeyNoOrg = "sk-agentcc-mcp-no-org"

	mcpUnknownSession = "00000000000000000000000000000000"
)

func newMCPSessionServer(t *testing.T) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "mcp-org-a", Key: mcpKeyA, Owner: "a", KeyType: "internal", Metadata: map[string]string{"org_id": mcpOrgA}},
		{Name: "mcp-org-a-second", Key: mcpKeyA2, Owner: "a", KeyType: "internal", Metadata: map[string]string{"org_id": mcpOrgA}},
		{Name: "mcp-org-b", Key: mcpKeyB, Owner: "b", KeyType: "internal", Metadata: map[string]string{"org_id": mcpOrgB}},
		{Name: "mcp-no-org", Key: mcpKeyNoOrg, Owner: "ops", KeyType: "internal"},
	}
	cfg.MCP.Enabled = true
	cfg.MCP.SessionTTL = 5 * time.Minute
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

// mcpServe sends one request to the gateway. A GET that is allowed streams
// until the timeout; one that is refused returns at once.
func mcpServe(t *testing.T, srv *Server, method, key, sessionID, body string, timeout time.Duration) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, "/mcp", bytes.NewBufferString(body))
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Authorization", "Bearer "+key)
	if sessionID != "" {
		req.Header.Set("MCP-Session-Id", sessionID)
	}
	ctx, cancel := context.WithTimeout(req.Context(), timeout)
	defer cancel()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	return w
}

func mcpOpenSession(t *testing.T, srv *Server, key string) string {
	t.Helper()
	w := mcpServe(t, srv, "POST", key, "",
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"t","version":"1"},"capabilities":{}}}`,
		5*time.Second)
	id := w.Header().Get("MCP-Session-Id")
	if w.Code != http.StatusOK || id == "" {
		t.Fatalf("initialize with %s: status %d session %q body %s", key, w.Code, id, w.Body.String())
	}
	return id
}

// sessionCalls are the JSON-RPC methods that run inside a session.
var sessionCalls = map[string]string{
	"tools/list":     `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`,
	"tools/call":     `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"x_y","arguments":{}}}`,
	"resources/list": `{"jsonrpc":"2.0","id":2,"method":"resources/list"}`,
	"resources/read": `{"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":"file:///x"}}`,
	"prompts/list":   `{"jsonrpc":"2.0","id":2,"method":"prompts/list"}`,
	"prompts/get":    `{"jsonrpc":"2.0","id":2,"method":"prompts/get","params":{"name":"x_y"}}`,
}

// requireLikeUnknownSession asserts w is what the same call gets with a
// session id that was never issued: so another key's session id reveals
// nothing about that session.
func requireLikeUnknownSession(t *testing.T, w, unknown *httptest.ResponseRecorder, what string) {
	t.Helper()
	if unknown.Code != http.StatusNotFound {
		t.Fatalf("%s: an unknown session answered %d, want 404", what, unknown.Code)
	}
	if w.Code != unknown.Code || !bytes.Equal(w.Body.Bytes(), unknown.Body.Bytes()) {
		t.Fatalf("%s: status %d body %q; an unknown session gets %d %q", what, w.Code, w.Body.String(), unknown.Code, unknown.Body.String())
	}
}

func TestMCPSession_AnotherKeyCannotAttachTheStream(t *testing.T) {
	srv := newMCPSessionServer(t)
	sessionA := mcpOpenSession(t, srv, mcpKeyA)

	for _, key := range []string{mcpKeyB, mcpKeyA2, mcpKeyNoOrg} {
		w := mcpServe(t, srv, "GET", key, sessionA, "", 300*time.Millisecond)
		unknown := mcpServe(t, srv, "GET", key, mcpUnknownSession, "", 300*time.Millisecond)
		requireLikeUnknownSession(t, w, unknown, fmt.Sprintf("GET /mcp with key %s on org A's session", key))
		if ct := w.Header().Get("Content-Type"); ct == "text/event-stream" {
			t.Fatalf("GET /mcp with key %s on org A's session opened an event stream", key)
		}
	}

	// The owner still attaches its stream.
	w := mcpServe(t, srv, "GET", mcpKeyA, sessionA, "", 200*time.Millisecond)
	if w.Code != http.StatusOK || w.Header().Get("Content-Type") != "text/event-stream" {
		t.Fatalf("GET /mcp by the session's own key: status %d content-type %q body %s", w.Code, w.Header().Get("Content-Type"), w.Body.String())
	}
}

func TestMCPSession_AnotherKeyCannotCallInsideTheSession(t *testing.T) {
	srv := newMCPSessionServer(t)
	sessionA := mcpOpenSession(t, srv, mcpKeyA)

	for method, body := range sessionCalls {
		for _, key := range []string{mcpKeyB, mcpKeyA2, mcpKeyNoOrg} {
			w := mcpServe(t, srv, "POST", key, sessionA, body, 5*time.Second)
			unknown := mcpServe(t, srv, "POST", key, mcpUnknownSession, body, 5*time.Second)
			requireLikeUnknownSession(t, w, unknown, fmt.Sprintf("%s with key %s on org A's session", method, key))
		}
	}

	// The session is intact for its owner.
	w := mcpServe(t, srv, "POST", mcpKeyA, sessionA, sessionCalls["tools/list"], 5*time.Second)
	if w.Code != http.StatusOK {
		t.Fatalf("tools/list by the session's own key after the other keys' calls: status %d body %s", w.Code, w.Body.String())
	}
	var resp struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || resp.Result == nil || resp.Error != nil {
		t.Fatalf("tools/list by the session's own key: %s", w.Body.String())
	}
}

// A session opened with no org key is not a shared session either.
func TestMCPSession_NoOrgSessionIsBoundToItsKey(t *testing.T) {
	srv := newMCPSessionServer(t)
	session := mcpOpenSession(t, srv, mcpKeyNoOrg)
	for _, key := range []string{mcpKeyA, mcpKeyB} {
		w := mcpServe(t, srv, "POST", key, session, sessionCalls["tools/list"], 5*time.Second)
		unknown := mcpServe(t, srv, "POST", key, mcpUnknownSession, sessionCalls["tools/list"], 5*time.Second)
		requireLikeUnknownSession(t, w, unknown, "tools/list with key "+key+" on a no-org session")
	}
	w := mcpServe(t, srv, "POST", mcpKeyNoOrg, session, sessionCalls["tools/list"], 5*time.Second)
	if w.Code != http.StatusOK {
		t.Fatalf("tools/list by the no-org session's own key: status %d body %s", w.Code, w.Body.String())
	}
}
