package mcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"
)

// The MCP HTTP auth chokepoint (W2b integration, IC-6): with per-key auth set,
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

	// A valid key passes the chokepoint: DELETE reaches its handler. Only
	// "not 401" is asserted: M12 serves DELETE in this wave (405 before it,
	// its session answer after), and the gate reruns this on M12's handler.
	req := httptest.NewRequest(http.MethodDelete, "/mcp", nil)
	req.Header.Set("Authorization", "Bearer my-key")
	req.Header.Set("MCP-Session-Id", sessionID)
	w := httptest.NewRecorder()
	s.HandleDelete(w, req)
	if w.Code == http.StatusUnauthorized {
		t.Fatalf("DELETE /mcp with a valid key: status 401, want the handler's answer")
	}
}

// The gateway's own identity (M8) refuses the same way: every method, an
// unknown one and GET and DELETE answer 401 missing_credentials with no key,
// and invalid_api_key with a bad one, before a session is touched.
func TestMCPEveryMethodWithoutAKeyIs401WithTheGatewayIdentity(t *testing.T) {
	s, _ := newIdentityServer(t, false)
	sessionID := initializeSessionWith(t, s, bearer(m8KeyShared))
	methods := make([]string, 0, len(methodTable)+1)
	for m := range methodTable {
		methods = append(methods, m)
	}
	sort.Strings(methods)
	methods = append(methods, "no/such/method")
	for _, method := range methods {
		msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`9`), Method: method, Params: json.RawMessage(`{}`)}
		requireAuthError(t, postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID}), AuthMissingCredentials, "POST "+method)
		requireAuthError(t, postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID, "x-api-key": "not-a-key"}), AuthInvalidAPIKey, "POST "+method+" with a bad key")
	}
	if n := s.SessionCount(); n != 1 {
		t.Fatalf("refused requests changed the session count to %d, want 1", n)
	}
	w := mcpRequest(s, http.MethodDelete, withHeaders(bearer(m8KeyShared), "MCP-Session-Id", sessionID))
	if w.Code == http.StatusUnauthorized {
		t.Fatal("DELETE with a valid key: 401, want the handler's answer")
	}
}
