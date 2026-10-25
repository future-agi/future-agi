package mcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"
)

// The MCP HTTP auth chokepoint: with per-key auth set,
// every JSON-RPC method the dispatch table serves, an unknown method, and GET
// and DELETE on the endpoint answer 401 with a Bearer challenge when no key is
// sent, before anything is parsed or a session is touched.
func TestMCPEveryMethodWithoutAKeyIs401(t *testing.T) {
	s := newKeyedTestServer(t)
	sessionID := initializeSessionWith(t, s, map[string]string{"Authorization": "Bearer my-key"})

	methods := make([]string, 0, len(methodTable)+1)
	for m := range methodTable {
		methods = append(methods, m)
	}
	sort.Strings(methods)
	methods = append(methods, "no/such/method")
	if len(methods) < 20 {
		t.Fatalf("dispatch table has %d methods, expected the full two-era table", len(methods)-1)
	}
	for _, method := range methods {
		msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`9`), Method: method, Params: json.RawMessage(`{}`)}
		for _, headers := range []map[string]string{
			{"MCP-Session-Id": sessionID},
			{"MCP-Session-Id": sessionID, "Authorization": "Bearer not-a-key"},
			{"MCP-Session-Id": sessionID, "x-api-key": "not-a-key"},
		} {
			requireUnauthorized(t, postMCP(t, s, msg, headers), "POST "+method)
		}
	}
	if n := s.SessionCount(); n != 1 {
		t.Fatalf("keyless requests changed the session count to %d, want 1", n)
	}

	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		req := httptest.NewRequest(method, "/mcp", nil)
		req.Header.Set("MCP-Session-Id", sessionID)
		w := httptest.NewRecorder()
		if method == http.MethodGet {
			s.HandleSSE(w, req)
		} else {
			s.HandleDelete(w, req)
		}
		requireUnauthorized(t, w, method+" /mcp with no key")
	}

	// A valid key passes the chokepoint: DELETE reaches its handler, which
	// answers 405.
	req := httptest.NewRequest(http.MethodDelete, "/mcp", nil)
	req.Header.Set("Authorization", "Bearer my-key")
	w := httptest.NewRecorder()
	s.HandleDelete(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("DELETE /mcp with a valid key: status %d, want 405 from the handler", w.Code)
	}
}
