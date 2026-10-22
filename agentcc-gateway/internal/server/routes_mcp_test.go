package server

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// mcpTestStateKey is a fixed MCP state key (at least 32 bytes). Config
// validation refuses mcp.enabled without one; New does not validate, so
// this keeps every MCP-enabled test valid under that rule.
const mcpTestStateKey = "mcp-test-state-key-000000000000000000000000"

// newMCPTestServer builds a gateway with MCP on or off.
func newMCPTestServer(t *testing.T, enabled bool, mutate func(*config.Config)) *Server {
	t.Helper()
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "mcp-admin"
	cfg.MCP.Enabled = enabled
	if mutate != nil {
		mutate(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	t.Cleanup(func() {
		if m := srv.MCP(); m != nil {
			m.Close()
		}
	})
	return srv
}

// mcpInitialize posts an initialize to the MCP endpoint and returns the
// session id.
func mcpInitialize(t *testing.T, base string) string {
	t.Helper()
	body := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}`
	resp, err := http.Post(base+"/mcp", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatalf("initialize: %v", err)
	}
	defer resp.Body.Close()
	session := resp.Header.Get("MCP-Session-Id")
	if resp.StatusCode != http.StatusOK || session == "" {
		b, _ := io.ReadAll(resp.Body)
		t.Fatalf("initialize = %d %s, want 200 with a session", resp.StatusCode, b)
	}
	return session
}

func TestMCPRouteStubsRegistered(t *testing.T) {
	want := []string{
		"mcp_admin", "mcp_vs", "mcp_legacy_sse", "mcp_probe",
		"mcp_revisions", "mcp_oauth", "mcp_credentials", "mcp_approvals",
	}
	var names []string
	for _, rs := range mcpRouteStubs {
		names = append(names, rs.name)
	}
	if strings.Join(names, ",") != strings.Join(want, ",") {
		t.Fatalf("mcp route stubs = %v, want %v", names, want)
	}

	real := mcpRouteStubs
	t.Cleanup(func() { mcpRouteStubs = real })
	var calls []string
	mcpRouteStubs = make([]routeStub, len(real))
	for i, rs := range real {
		mcpRouteStubs[i] = routeStub{rs.name, func(s *Server, router *Router) {
			if s.MCP() == nil {
				t.Errorf("%s registered before the MCP server was built", rs.name)
			}
			calls = append(calls, rs.name)
			rs.register(s, router)
		}}
	}

	off := newMCPTestServer(t, false, nil)
	if off.MCP() != nil || len(calls) != 0 {
		t.Fatalf("MCP disabled: server %v, stubs called %v; want neither", off.MCP(), calls)
	}
	if w := serve(off, "POST", "/mcp", `{}`, ""); w.Code != http.StatusNotFound {
		t.Fatalf("MCP disabled: POST /mcp = %d, want 404", w.Code)
	}

	srv := newMCPTestServer(t, true, nil)
	if strings.Join(calls, ",") != strings.Join(want, ",") {
		t.Fatalf("New registered %v, want each of %v once, in order", calls, want)
	}
	if srv.MCP() == nil {
		t.Fatal("MCP enabled but MCP() is nil")
	}

	// The MCP routes answer on the endpoint.
	init := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}`
	w := serve(srv, "POST", "/mcp", init, "")
	if w.Code != http.StatusOK || w.Header().Get("MCP-Session-Id") == "" {
		t.Fatalf("POST /mcp initialize = %d %s", w.Code, w.Body.String())
	}
	if w := serve(srv, "GET", "/mcp", "", ""); w.Code != http.StatusBadRequest {
		t.Fatalf("GET /mcp without a session = %d, want 400", w.Code)
	}
	// DELETE answers with the same 405 the router gives a method it does not route.
	del := serve(srv, "DELETE", "/mcp", "", "")
	unrouted := serve(srv, "PATCH", "/mcp", "", "")
	if del.Code != http.StatusMethodNotAllowed || del.Body.String() != unrouted.Body.String() {
		t.Fatalf("DELETE /mcp = %d %s, want the router's 405 %s", del.Code, del.Body.String(), unrouted.Body.String())
	}
	for _, path := range []string{"/-/mcp/status", "/-/mcp/tools", "/-/mcp/resources", "/-/mcp/prompts"} {
		if w := serve(srv, "GET", path, "", "Bearer mcp-admin"); w.Code != http.StatusOK {
			t.Errorf("GET %s = %d %s, want 200", path, w.Code, w.Body.String())
		}
		if w := serve(srv, "GET", path, "", ""); w.Code != http.StatusUnauthorized {
			t.Errorf("GET %s without the admin token = %d, want 401", path, w.Code)
		}
	}
	if w := serve(srv, "GET", "/-/mcp/status", "", "Bearer mcp-admin"); !strings.Contains(w.Body.String(), `"enabled":true`) {
		t.Errorf("status = %s", w.Body.String())
	}
	if w := serve(srv, "POST", "/-/mcp/test", `{"name":"nope"}`, "Bearer mcp-admin"); w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "tool not found: nope") {
		t.Errorf("POST /-/mcp/test = %d %s", w.Code, w.Body.String())
	}

	// A custom endpoint moves every MCP route with it.
	custom := newMCPTestServer(t, true, func(c *config.Config) { c.MCP.Endpoint = "/tools/mcp" })
	if w := serve(custom, "POST", "/tools/mcp", init, ""); w.Code != http.StatusOK {
		t.Fatalf("POST /tools/mcp = %d", w.Code)
	}
	if w := serve(custom, "POST", "/mcp", init, ""); w.Code != http.StatusNotFound {
		t.Fatalf("POST /mcp with a custom endpoint = %d, want 404", w.Code)
	}
}

func serve(srv *Server, method, path, body, auth string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// TestMCPStreamOutlivesDefaultTimeout: with a 1 s request timeout the GET
// /mcp stream stays open. The middleware holds every other route at
// server.max_request_timeout rather than the default, so both are 1 s here:
// the stream outlives the ceiling too.
func TestMCPStreamOutlivesDefaultTimeout(t *testing.T) {
	srv := newMCPTestServer(t, true, func(c *config.Config) {
		c.Server.DefaultRequestTimeout = time.Second
		c.Server.MaxRequestTimeout = time.Second
	})
	ts := httptest.NewServer(srv.httpServer.Handler)
	defer ts.Close()
	session := mcpInitialize(t, ts.URL)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, ts.URL+"/mcp", nil)
	req.Header.Set("MCP-Session-Id", session)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET /mcp: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK || resp.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("GET /mcp = %d %s, want an event stream", resp.StatusCode, resp.Header.Get("Content-Type"))
	}

	lines := make(chan string, 16)
	ended := make(chan error, 1)
	go func() {
		sc := bufio.NewScanner(resp.Body)
		for sc.Scan() {
			lines <- sc.Text()
		}
		ended <- sc.Err()
	}()
	select {
	case err := <-ended:
		t.Fatalf("the stream ended after less than 3s (err %v): the default request timeout cut it", err)
	case <-time.After(3 * time.Second):
	}

	// The stream still delivers after the default timeout: unregistering a
	// server sends notifications/tools/list_changed to it.
	srv.MCP().UnregisterClient("none")
	deadline := time.After(2 * time.Second)
	for got := false; !got; {
		select {
		case line := <-lines:
			got = strings.HasPrefix(line, "data: ") && strings.Contains(line, "notifications/tools/list_changed")
		case err := <-ended:
			t.Fatalf("the stream ended before the notification (err %v)", err)
		case <-deadline:
			t.Fatal("no tools/list_changed notification on the stream")
		}
	}
	cancel()

	if prefixes := mcpTimeoutPrefixes(srv.cfg); strings.Join(prefixes, ",") != "/mcp,/mcp/,/.well-known/oauth-protected-resource/" {
		t.Fatalf("timeout prefixes = %v", prefixes)
	}
	if mcpTimeoutPrefixes(newMCPTestServer(t, false, nil).cfg) != nil {
		t.Fatal("timeout prefixes set with MCP disabled")
	}
}

// TestMCPServerAccessorHandsDepsToTheChain checks that what the wiring hands
// over through MCP().SetDeps reaches the server the routes serve.
func TestMCPServerAccessorHandsDepsToTheChain(t *testing.T) {
	srv := newMCPTestServer(t, true, nil)
	var seen []string
	srv.MCP().SetDeps(mcp.Deps{Events: func(kind string, _ any) { seen = append(seen, kind) }})
	srv.MCP().Deps().Events("tool_call", nil)
	if len(seen) != 1 || seen[0] != "tool_call" {
		t.Fatalf("events seen %v", seen)
	}
	var status map[string]any
	if err := json.Unmarshal(serve(srv, "GET", "/-/mcp/status", "", "Bearer mcp-admin").Body.Bytes(), &status); err != nil || status["enabled"] != true {
		t.Fatalf("status = %v, %v", status, err)
	}
}
