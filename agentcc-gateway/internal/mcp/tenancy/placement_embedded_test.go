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

// Close's API-key
// provider as the catalog records it, read from the catalog the gateway embeds. A
// server added from it (catalog_ref close/api-key@1.0.0, as Django writes it)
// whose push carries only the secret Close-API-Key reaches the upstream with
// both headers on every request of the session, through the pool's real
// client: the key, and the constant Close-Scope: mcp.read, which the gateway
// adds from the catalog and the push never carries. The same server
// with the constant pushed, or with another major version, is refused.
func TestEmbeddedCloseKeyReachesTheUpstreamWithItsScope(t *testing.T) {
	const key = "test-close-key-not-a-real-key"
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
	mock, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "close"}, fix)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var served int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Values("Close-API-Key"); len(got) != 1 || got[0] != key {
			http.Error(w, "missing or wrong Close-API-Key", http.StatusUnauthorized)
			return
		}
		if got := r.Header.Values("Close-Scope"); len(got) != 1 || got[0] != "mcp.read" {
			http.Error(w, "missing or wrong Close-Scope", http.StatusForbidden)
			return
		}
		if r.Header.Get("Authorization") != "" {
			http.Error(w, "an Authorization header reached Close", http.StatusBadRequest)
			return
		}
		mu.Lock()
		served++
		mu.Unlock()
		mock.Handler().ServeHTTP(w, r)
	}))
	defer srv.Close()

	orgConfig := func(ref string, secret map[string]any) *tenant.OrgConfig {
		body, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
			"enabled": true, "servers": map[string]any{"close": map[string]any{"url": srv.URL + "/mcp", "catalog_ref": ref,
				"credential": map[string]any{"mode": "shared", "credential_id": "cred_close", "secret": secret}}}}})
		var cfg tenant.OrgConfig
		if err := json.Unmarshal(body, &cfg); err != nil {
			t.Fatal(err)
		}
		return &cfg
	}
	keyOnly := map[string]any{"type": "api_key", "header": "Close-API-Key", "in": "header", "name": "Close-API-Key", "value": key}
	store := tenant.NewStore()
	store.Set("org-close", orgConfig("close/api-key@1.0.0", keyOnly))
	store.Set("org-scope-pushed", orgConfig("close/api-key@1.0.0", map[string]any{"type": "api_key", "header": "Close-API-Key",
		"in": "header", "name": "Close-API-Key", "value": key, "parts": []any{map[string]any{"in": "header", "name": "Close-Scope", "value": "mcp.write"}}}))
	store.Set("org-major", orgConfig("close/api-key@2.0.0", keyOnly))
	m := New(Options{Store: store, Catalog: cat, MCP: config.MCPConfig{CatalogEnabled: true}, InitialWait: 10 * time.Second, Deps: func() *mcp.Deps {
		return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}}
	}})
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = m.Close(ctx)
	}()

	_, pool, err := m.Resolve("org-close")
	if err != nil {
		t.Fatal(err)
	}
	uc, err := pool.Client("close", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatalf("the pool has no client for close: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	res, err := uc.CallTool(ctx, "echo", json.RawMessage(`{"text":"close"}`), mcp.CallOptions{})
	if err != nil || !strings.Contains(string(res), "close") {
		t.Fatalf("tools/call through the pool: %s, %v", res, err)
	}
	mu.Lock()
	n := served
	mu.Unlock()
	if n < 2 {
		t.Fatalf("Close served %d requests with both headers, want the whole session", n)
	}

	for org, field := range map[string]string{"org-scope-pushed": "credential.secret.parts[0].name", "org-major": "catalog_ref"} {
		if _, _, err := m.Resolve(org); !errors.Is(err, ErrNoLastKnownGood) || !strings.Contains(err.Error(), field) || strings.Contains(err.Error(), key) {
			t.Errorf("%s: Resolve err %v; want ErrNoLastKnownGood naming %s, without the key", org, err, field)
		}
	}
}
