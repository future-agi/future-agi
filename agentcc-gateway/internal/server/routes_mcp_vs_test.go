package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// TestMCPVirtualServerRoutes checks that {endpoint}/v/{vs_id} reaches the MCP
// server as an address to that virtual server. With no org config (no MCP
// principal and tenant on this branch) every vs_id is unknown, so a
// chained method answers the virtual-server 404 while the plain endpoint,
// whatever its query string, serves the legacy view.
func TestMCPVirtualServerRoutes(t *testing.T) {
	for _, endpoint := range []string{"/mcp", "/tools/mcp"} {
		srv := newMCPTestServer(t, true, func(c *config.Config) { c.MCP.Endpoint = endpoint })
		init := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}`
		w := serve(srv, "POST", endpoint+"/v/vs_01M4ZEB9E0J3AADR85Z4T4428X", init, "")
		session := w.Header().Get("MCP-Session-Id")
		if w.Code != http.StatusOK || session == "" {
			t.Fatalf("%s: initialize on a virtual-server path = %d %s", endpoint, w.Code, w.Body.String())
		}
		list := func(path string) (int, string) {
			req := `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`
			rec := serveWithSession(srv, path, req, session)
			return rec.Code, rec.Body.String()
		}
		code, body := list(endpoint + "/v/vs_01M4ZEB9E0J3AADR85Z4T4428X")
		if code != http.StatusNotFound || !strings.Contains(body, "virtual server not found") {
			t.Fatalf("%s: tools/list on an unknown virtual server = %d %s, want 404", endpoint, code, body)
		}
		var resp struct {
			Error *struct{ Code int } `json:"error"`
		}
		if err := json.Unmarshal([]byte(body), &resp); err != nil || resp.Error == nil || resp.Error.Code != -32600 {
			t.Fatalf("%s: 404 body %s", endpoint, body)
		}
		// A session is bound to the virtual server it was opened on, so
		// the plain endpoint's check opens its own session there.
		w = serve(srv, "POST", endpoint, init, "")
		if session = w.Header().Get("MCP-Session-Id"); w.Code != http.StatusOK || session == "" {
			t.Fatalf("%s: initialize = %d %s", endpoint, w.Code, w.Body.String())
		}
		if code, body := list(endpoint + "?vs_id=vs_01M4ZEB9E0J3AADR85Z4T4428X"); code != http.StatusOK || strings.Contains(body, "error") {
			t.Fatalf("%s?vs_id= = %d %s, want the plain endpoint", endpoint, code, body)
		}
	}
}

func serveWithSession(srv *Server, path, body, session string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("POST", path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("MCP-Session-Id", session)
	rec := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(rec, req)
	return rec
}
