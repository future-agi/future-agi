package main

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream"
)

// Only the owned loopback runtime is dialable. The source URL stays exactly
// what the real K2 policy admitted; no production DNS or provider is contacted.
type k2R6LoopbackDialer struct{ target string }

func (d k2R6LoopbackDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	if address != "127.0.0.1:9" {
		return nil, errors.New("unexpected non-fixture dial")
	}
	return (&net.Dialer{}).DialContext(ctx, network, d.target)
}

// K2 pins personal grants: it has no autonomous OAuth refresh on a retained
// client. Refresh is the next credential lookup after cache invalidation. Test
// that actual HTTP chain and token resolver, not a mocked HeaderSource.
func TestK2R6BeforeTokenRefresh(t *testing.T) {
	for _, mutation := range []string{"catalog", "ref", "url"} {
		t.Run(mutation, func(t *testing.T) {
			var sends atomic.Int64
			runtime := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Authorization") != "Bearer "+k2BoundaryToken {
					http.Error(w, "missing synthetic auth", 401)
					return
				}
				sends.Add(1)
				var req struct {
					ID     json.RawMessage `json:"id"`
					Method string          `json:"method"`
				}
				if json.NewDecoder(r.Body).Decode(&req) != nil {
					http.Error(w, "bad rpc", 400)
					return
				}
				if len(req.ID) == 0 {
					w.WriteHeader(202)
					return
				}
				var result any = map[string]any{}
				switch req.Method {
				case "initialize":
					result = map[string]any{"protocolVersion": "2025-11-25", "serverInfo": map[string]any{"name": "synthetic", "version": "1"}, "capabilities": map[string]any{}}
				case "tools/list":
					result = map[string]any{"tools": []any{map[string]any{"name": "echo", "inputSchema": map[string]any{"type": "object"}}}}
				case "resources/list":
					result = map[string]any{"resources": []any{}}
				case "prompts/list":
					result = map[string]any{"prompts": []any{}}
				case "tools/call":
					result = map[string]any{"content": []any{map[string]any{"type": "text", "text": "synthetic success"}}}
				}
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(map[string]any{"jsonrpc": "2.0", "id": req.ID, "result": result})
			}))
			t.Cleanup(runtime.Close)
			cat := k2ConstructionCatalog(t, nil)
			p := cat.App("google-docs").Provider("futureagi-rest")
			ref := "google-docs/futureagi-rest@" + p.Version
			f := newK2BoundaryFixture(t, true, ref, "google-docs")
			factory := upstream.NewFactory(upstream.Options{})
			deps := *f.srv.Deps()
			deps.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
				cfg.Dialer = k2R6LoopbackDialer{strings.TrimPrefix(runtime.URL, "http://")}
				cfg.PingInterval = time.Hour
				cfg.ToolsCacheTTL = time.Hour
				return factory.Build(cfg)
			}
			f.srv.SetDeps(deps)
			success := func() {
				a := f.call(t)
				if a.IsError || a.Denial.Code != "" {
					t.Fatalf("positive real dispatch refused: %+v", a.Denial)
				}
			}
			success()
			purger, ok := f.srv.Deps().CredentialResolver.(mcp.CredentialPurger)
			if !ok {
				t.Fatal("missing real credential purger")
			}
			before := f.real.fetches.Load()
			if _, err := purger.PurgeCredentials(t.Context(), "org-a", "user-a", "service"); err != nil {
				t.Fatal(err)
			}
			success()
			if f.real.fetches.Load() <= before {
				t.Fatal("enabled refresh positive never fetched a new token")
			}
			if _, err := purger.PurgeCredentials(t.Context(), "org-a", "user-a", "service"); err != nil {
				t.Fatal(err)
			}
			before = f.real.fetches.Load()
			sent := sends.Load()
			reason := "k2_connection_disabled"
			switch mutation {
			case "catalog":
				p.XGateway.ConnectionEnablement.Enabled = false
			case "ref":
				reason = "k2_client_source_unavailable"
				k2PushServers(t, f.real, 2, map[string]any{"service": k2PerUserServer(k2TestRuntime+"/google-docs/mcp", "google-docs/futureagi-rest@9.9.9"), "broken": k2PerUserServer("not-a-url", "")})
			case "url":
				reason = "k2_endpoint_unavailable"
				k2PushServers(t, f.real, 2, map[string]any{"service": k2PerUserServer(k2TestRuntime+"/wrong/mcp", ref), "broken": k2PerUserServer("not-a-url", "")})
			}
			a := f.call(t)
			if !a.IsError || a.Denial.Code != mcp.CodePolicyUnavailable || !strings.Contains(a.Denial.Message, "Coming soon") {
				t.Fatalf("not typed Coming soon: %+v", a.Denial)
			}
			meta, ok := a.Denial.Metadata.(map[string]any)
			if !ok || meta["reason"] != reason {
				t.Errorf("wrong reason: %+v", a.Denial.Metadata)
			}
			if f.real.fetches.Load() != before || sends.Load() != sent {
				t.Errorf("refusal refreshed token or sent upstream: fetch delta=%d send delta=%d", f.real.fetches.Load()-before, sends.Load()-sent)
			}
		})
	}
}
