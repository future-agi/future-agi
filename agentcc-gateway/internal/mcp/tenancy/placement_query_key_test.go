package tenancy

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate/catalogparts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// TestEmbeddedQueryKeyProvidersReachTheUpstream: Tavily's and Browserbase's
// API-key providers, read from the catalog the gateway embeds, take the key
// as a query parameter of the MCP URL. A server added from one (catalog_ref
// <app>/api-key@1.0.0, as Django writes it) whose push carries the key in
// that placement reaches the upstream with <name>=<key> on every request of
// the session, through the pool's real client: URL-encoded, exactly once,
// never in a header. Two organizations on the same upstream each send only
// their own key. The same server pushed with its key in a header is refused,
// as the catalog declares a query parameter, and the refusal names no key.
func TestEmbeddedQueryKeyProvidersReachTheUpstream(t *testing.T) {
	embedded, err := connectors.Embedded()
	if err != nil {
		t.Fatal(err)
	}
	cat := catalogparts.FromManifest(embedded.Records)
	fix, err := mcpmock.LoadFixtures("../../../scripts/scenarios/fixtures/mcp/common")
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}

	for _, c := range []struct{ app, name string }{{"tavily", "tavilyApiKey"}, {"browserbase", "browserbaseApiKey"}} {
		t.Run(c.app, func(t *testing.T) {
			mock, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: c.app}, fix)
			if err != nil {
				t.Fatal(err)
			}
			keys := map[string]string{
				"org-a": "test-" + c.app + "-key-a/7+=&?",
				"org-b": "test-" + c.app + "-key-b/7+=&?",
			}
			var mu sync.Mutex
			served := map[string]int{}
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				q := r.URL.Query()
				got := q[c.name]
				owner := ""
				for org, key := range keys {
					if len(got) == 1 && got[0] == key {
						owner = org
					}
					for _, values := range r.Header {
						for _, v := range values {
							if strings.Contains(v, key) {
								http.Error(w, "a key reached a header", http.StatusBadRequest)
								return
							}
						}
					}
				}
				if owner == "" {
					http.Error(w, "missing or wrong key", http.StatusUnauthorized)
					return
				}
				mu.Lock()
				served[owner]++
				mu.Unlock()
				q.Del(c.name)
				r.URL.RawQuery = q.Encode()
				mock.Handler().ServeHTTP(w, r)
			}))
			defer srv.Close()

			orgConfig := func(secret map[string]any) *tenant.OrgConfig {
				body, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
					"enabled": true, "servers": map[string]any{c.app: map[string]any{"url": srv.URL + "/mcp",
						"catalog_ref": c.app + "/api-key@1.0.0",
						"credential":  map[string]any{"mode": "shared", "credential_id": "cred_" + c.app, "secret": secret}}}}})
				var cfg tenant.OrgConfig
				if err := json.Unmarshal(body, &cfg); err != nil {
					t.Fatal(err)
				}
				return &cfg
			}
			const headerKey = "test-header-placed-key-not-real"
			store := tenant.NewStore()
			for org, key := range keys {
				store.Set(org, orgConfig(map[string]any{"type": "api_key", "in": "query", "name": c.name, "value": key}))
			}
			store.Set("org-header", orgConfig(map[string]any{"type": "api_key", "header": "X-Api-Key", "in": "header",
				"name": "X-Api-Key", "value": headerKey}))
			m := New(Options{Store: store, Catalog: cat, MCP: config.MCPConfig{CatalogEnabled: true}, InitialWait: 10 * time.Second,
				Deps: func() *mcp.Deps {
					return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}}
				}})
			defer func() {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				_ = m.Close(ctx)
			}()

			call := func(org string) {
				t.Helper()
				_, pool, err := m.Resolve(org)
				if err != nil {
					t.Fatalf("%s: %v", org, err)
				}
				uc, err := pool.Client(c.app, "", mcp.AuthOverride{})
				if err != nil {
					t.Fatalf("%s: the pool has no client for %s: %v", org, c.app, err)
				}
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				res, err := uc.CallTool(ctx, "echo", json.RawMessage(`{"text":"`+org+`"}`), mcp.CallOptions{})
				if err != nil || !strings.Contains(string(res), org) {
					t.Fatalf("%s: tools/call through the pool: %s, %v", org, res, err)
				}
			}
			counts := func() (int, int) {
				mu.Lock()
				defer mu.Unlock()
				return served["org-a"], served["org-b"]
			}

			call("org-a")
			if a, b := counts(); a < 2 || b != 0 {
				t.Fatalf("after org-a's session the upstream served %d requests with org-a's key and %d with org-b's; "+
					"want org-a's whole session and none of org-b's", a, b)
			}
			call("org-b")
			if _, b := counts(); b < 2 {
				t.Fatalf("the upstream served %d requests with org-b's key, want org-b's whole session", b)
			}

			if _, _, err := m.Resolve("org-header"); !errors.Is(err, ErrNoLastKnownGood) || strings.Contains(err.Error(), headerKey) {
				t.Errorf("a %s server with its key pushed in a header: Resolve err %v; want ErrNoLastKnownGood, without the key", c.app, err)
			}
		})
	}
}
