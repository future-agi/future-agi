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

// With per-key auth set, a request with no key used to skip auth and see
// every tool unfiltered. Every JSON-RPC method must now be refused.
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

// The gateway accepts a key as "Authorization: Bearer" or as x-api-key. A key
// sent as x-api-key used to be invisible to the MCP server, so its tool
// allow-list was never applied.
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

// GET /mcp opens the server-to-client stream. It used to need only a session
// ID; with per-key auth set it needs a valid key too.
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

// TestNoAuthBypassWhenAuthDisabled: the gateway installs its identity on the
// MCP server whatever auth.enabled says (wiring_mcp_identity.go). A request
// with no credential is served only where the release line served it
// (TestNoKeyAuthServesWithoutAKey: request auth off, the operator's
// servers bound to no org, no virtual server addressed) and under
// mcp.allow_anonymous_dev (tests only), each with no principal. Anywhere
// else it is 401 on every method, here an identity that serves no keyless
// request (request auth on, or the operator's servers bound to an org by
// mcp.static.org_id, as newIdentityServer binds them). A credential that is
// sent is checked either way.
func TestNoAuthBypassWhenAuthDisabled(t *testing.T) {
	t.Run("an identity that serves no keyless request: 401 on every method", func(t *testing.T) {
		s, _ := newIdentityServer(t, false)
		initParams, _ := json.Marshal(InitializeParams{ProtocolVersion: ProtocolVersion, ClientInfo: Implementation{Name: "anon", Version: "1.0"}})
		init := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodInitialize, Params: initParams}
		requireAuthError(t, postMCP(t, s, init, nil), AuthMissingCredentials, "keyless initialize")
		requireAuthError(t, postMCP(t, s, toolsListMsg(), nil), AuthMissingCredentials, "keyless tools/list")
		requireAuthError(t, mcpRequest(s, http.MethodGet, nil), AuthMissingCredentials, "keyless GET")
		requireAuthError(t, mcpRequest(s, http.MethodDelete, nil), AuthMissingCredentials, "keyless DELETE")
		if n := s.SessionCount(); n != 0 {
			t.Fatalf("keyless requests opened %d sessions", n)
		}
		// A valid key is served.
		sessionID := initializeSessionWith(t, s, bearer(m8KeyShared))
		w := postMCP(t, s, toolsListMsg(), withHeaders(bearer(m8KeyShared), "MCP-Session-Id", sessionID))
		if resp := decodeResponse(t, w); resp.Error != nil {
			t.Fatalf("a valid key: %s", resp.Error.Message)
		}
	})

	t.Run("mcp.allow_anonymous_dev serves a keyless request with no principal", func(t *testing.T) {
		s, _ := newIdentityServer(t, true)
		sessionID := initializeSession(t, s)
		p, w := principalAt(t, s, map[string]string{"MCP-Session-Id": sessionID})
		if resp := decodeResponse(t, w); resp.Error != nil || w.Code != http.StatusOK {
			t.Fatalf("anonymous dev tools/list: status %d, error %+v", w.Code, resp.Error)
		}
		if p.Kind != "" || p.KeyID != "" || p.OrgID != "" || p.UserID != "" {
			t.Fatalf("an anonymous request got principal %+v", p)
		}
		// A credential that is sent is still checked, before any session.
		requireAuthError(t, postMCP(t, s, toolsListMsg(), map[string]string{"MCP-Session-Id": sessionID, "Authorization": "Bearer not-a-key"}), AuthInvalidAPIKey, "a bad key under anonymous dev")
		// A valid key resolves its principal, on a session of its own: a
		// session serves only the principal that opened it (M12).
		keySession := initializeSessionWith(t, s, bearer(m8KeyShared))
		p, w = principalAt(t, s, withHeaders(bearer(m8KeyShared), "MCP-Session-Id", keySession))
		if w.Code != http.StatusOK || p.Kind != PrincipalKey || p.OrgID != m8Org {
			t.Fatalf("a valid key under anonymous dev: status %d, principal %+v", w.Code, p)
		}
	})

	t.Run("an identity with no key store refuses every key", func(t *testing.T) {
		s := newTestServer(t)
		defer s.Close()
		s.SetIdentity(Identity{})
		requireAuthError(t, postMCP(t, s, toolsListMsg(), nil), AuthMissingCredentials, "no key, no store")
		requireAuthError(t, postMCP(t, s, toolsListMsg(), bearer(m8KeyShared)), AuthInvalidAPIKey, "a key, no store")
	})
}

// keylessStaticServer is an MCP server with the identity the gateway installs
// when request auth is off (wiring_mcp_identity.go): KeylessStatic and the
// M8 key store, with two operator (YAML) tools bound to no org.
func keylessStaticServer(t *testing.T) *Server {
	t.Helper()
	s := newTestServer(t)
	t.Cleanup(s.Close)
	s.Registry().RegisterServer("gh", []Tool{
		{Name: "t1", InputSchema: json.RawMessage(`{}`)},
		{Name: "t2", InputSchema: json.RawMessage(`{}`)},
	})
	s.SetIdentity(Identity{Keys: newM8KeyStore(t), KeylessStatic: true})
	return s
}

// listedToolNames is the tool names a tools/list answer lists, in order.
func listedToolNames(t *testing.T, w *httptest.ResponseRecorder, what string) []string {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("%s: status %d, body %s", what, w.Code, w.Body.String())
	}
	resp := decodeResponse(t, w)
	if resp.Error != nil {
		t.Fatalf("%s: error %+v", what, resp.Error)
	}
	var res ListToolsResult
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatalf("%s: result is not a tool list: %v", what, err)
	}
	names := make([]string, 0, len(res.Tools))
	for _, tool := range res.Tools {
		names = append(names, tool.Name)
	}
	return names
}

// TestNoKeyAuthServesWithoutAKey is the release line's test (c07c6f9fe2,
// auth_legacy_test.go), restored by MCP-APPS-PLAN ruling 7 ("/mcp for
// operator-config servers behaves as on release"): with gateway request
// auth off, the endpoint stays open to a request with no credential, which is
// served the operator's servers with no principal. That is all a keyless
// request reaches: a virtual server, or operator servers that
// mcp.static.org_id binds to an org, need an identity, so those answer 401.
// A credential that is sent is still checked, which is the one difference
// from the release line, where request auth off read no key on /mcp.
func TestNoKeyAuthServesWithoutAKey(t *testing.T) {
	t.Run("request auth off: a keyless request lists the operator's tools", func(t *testing.T) {
		s := keylessStaticServer(t)
		sessionID := initializeSession(t, s)
		p, w := principalAt(t, s, map[string]string{"MCP-Session-Id": sessionID})
		if got := strings.Join(listedToolNames(t, w, "keyless tools/list"), ","); got != "gh_t1,gh_t2" {
			t.Fatalf("keyless tools/list listed %q, want the operator's gh_t1,gh_t2", got)
		}
		if p.Kind != "" || p.KeyID != "" || p.OrgID != "" || p.UserID != "" {
			t.Fatalf("a keyless request got principal %+v, want none", p)
		}
		for _, method := range []string{http.MethodGet, http.MethodDelete} {
			if w := mcpRequest(s, method, map[string]string{"MCP-Session-Id": sessionID}); w.Code == http.StatusUnauthorized {
				t.Fatalf("keyless %s on its own session: 401 %s", method, w.Body.String())
			}
		}
	})

	t.Run("a credential that is sent is still checked", func(t *testing.T) {
		s := keylessStaticServer(t)
		requireAuthError(t, postMCP(t, s, toolsListMsg(), bearer("not-a-key")), AuthInvalidAPIKey, "a bad key with request auth off")
		requireAuthError(t, postMCP(t, s, toolsListMsg(), map[string]string{"x-api-key": m8KeyExpired}), AuthKeyExpired, "an expired key with request auth off")
		keySession := initializeSessionWith(t, s, bearer(m8KeyShared))
		p, w := principalAt(t, s, withHeaders(bearer(m8KeyShared), "MCP-Session-Id", keySession))
		if w.Code != http.StatusOK || p.Kind != PrincipalKey || p.OrgID != m8Org {
			t.Fatalf("a valid key with request auth off: status %d, principal %+v", w.Code, p)
		}
	})

	t.Run("a keyless request to a virtual server is 401", func(t *testing.T) {
		s := keylessStaticServer(t)
		body, _ := json.Marshal(toolsListMsg())
		req := httptest.NewRequest(http.MethodPost, "/mcp/v/vs-1", strings.NewReader(string(body)))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		s.HandlePost(w, WithVirtualServer(req, "vs-1"))
		requireAuthError(t, w, AuthMissingCredentials, "keyless tools/list on /mcp/v/vs-1")
	})

	t.Run("operator servers bound to an org: a keyless request is 401", func(t *testing.T) {
		s := keylessStaticServer(t)
		m8BindStaticOrg(s, m8Org)
		requireAuthError(t, postMCP(t, s, toolsListMsg(), nil), AuthMissingCredentials, "keyless tools/list with mcp.static.org_id set")
		if n := s.SessionCount(); n != 0 {
			t.Fatalf("refused keyless requests opened %d sessions", n)
		}
	})
}
