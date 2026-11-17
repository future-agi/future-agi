package tenancy

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
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

// prefixLogSink is a log sink safe for the pool's goroutines.
type prefixLogSink struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (s *prefixLogSink) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.Write(p)
}

func (s *prefixLogSink) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.String()
}

// TestEmbeddedPagerDutyKeyReachesTheUpstreamAfterItsPrefix: PagerDuty's
// API-key provider, read from the catalog the gateway embeds, declares its
// key in Authorization after "Token token=". A server added from it
// (catalog_ref pagerduty/api-key@1.0.0, as Django writes it) whose push
// carries the key alone, typed api_key in Authorization, as the vault
// stores it, reaches the upstream with "Authorization: Token token=<key>" on
// every request of the session, through the pool's real client: once,
// never "Bearer", never in the query. A push that also names a prefix is
// read without it and placed the same way; a bearer push is refused, as a
// bearer goes only as "Bearer ". Neither the log, at debug, nor the pool's
// server status holds the key.
func TestEmbeddedPagerDutyKeyReachesTheUpstreamAfterItsPrefix(t *testing.T) {
	const key = "test-pagerduty-key-not-real-9d2c"
	const want = "Token token=" + key
	logs := &prefixLogSink{}
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(old)

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
	mock, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "pagerduty"}, fix)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	served, wrong := 0, 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got := r.Header.Values("Authorization")
		inQuery := strings.Contains(r.URL.RawQuery, key)
		mu.Lock()
		if len(got) != 1 || got[0] != want || inQuery {
			wrong++
			mu.Unlock()
			http.Error(w, "missing or wrong key", http.StatusUnauthorized)
			return
		}
		served++
		mu.Unlock()
		mock.Handler().ServeHTTP(w, r)
	}))
	defer srv.Close()

	orgConfig := func(secret map[string]any) *tenant.OrgConfig {
		body, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
			"enabled": true, "servers": map[string]any{"pagerduty": map[string]any{"url": srv.URL + "/mcp",
				"catalog_ref": "pagerduty/api-key@1.0.0",
				"credential":  map[string]any{"mode": "shared", "credential_id": "cred_pagerduty", "secret": secret}}}}})
		var cfg tenant.OrgConfig
		if err := json.Unmarshal(body, &cfg); err != nil {
			t.Fatal(err)
		}
		return &cfg
	}
	store := tenant.NewStore()
	store.Set("org-pd", orgConfig(map[string]any{"type": "api_key", "header": "Authorization", "value": key}))
	store.Set("org-pd-prefix", orgConfig(map[string]any{"type": "api_key", "header": "Authorization", "value": key, "prefix": "Basic "}))
	store.Set("org-pd-bearer", orgConfig(map[string]any{"type": "bearer", "header": "Authorization", "value": key}))
	m := New(Options{Store: store, Catalog: cat, MCP: config.MCPConfig{CatalogEnabled: true}, InitialWait: 10 * time.Second,
		Deps: func() *mcp.Deps {
			return &mcp.Deps{ClientFactory: mcp.DefaultClientFactory, Builtins: mcp.NewBuiltinRegistry(), ManagedServers: noManaged{}}
		}})
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = m.Close(ctx)
	}()

	for _, org := range []string{"org-pd", "org-pd-prefix"} {
		mu.Lock()
		before := served
		mu.Unlock()
		_, pool, err := m.Resolve(org)
		if err != nil {
			t.Fatalf("%s: %v", org, strings.ReplaceAll(err.Error(), key, "<key>"))
		}
		uc, err := pool.Client("pagerduty", "", mcp.AuthOverride{})
		if err != nil {
			t.Fatalf("%s: the pool has no client for pagerduty: %v", org, err)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		res, err := uc.CallTool(ctx, "echo", json.RawMessage(`{"text":"`+org+`"}`), mcp.CallOptions{})
		cancel()
		if err != nil || !strings.Contains(string(res), org) {
			t.Fatalf("%s: tools/call through the pool: %s, %v", org, res, err)
		}
		mu.Lock()
		n := served - before
		mu.Unlock()
		if n < 2 {
			t.Fatalf("%s: the upstream served %d requests with Token token=<key>, want the whole session", org, n)
		}
		status, err := json.Marshal(pool.Servers())
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(status), key) {
			t.Errorf("%s: the pool's server status carries the key", org)
		}
	}
	mu.Lock()
	if wrong != 0 {
		t.Errorf("%d requests reached the upstream without exactly Authorization: Token token=<key>", wrong)
	}
	mu.Unlock()

	if _, _, err := m.Resolve("org-pd-bearer"); !errors.Is(err, ErrNoLastKnownGood) || !strings.Contains(err.Error(), "credential.secret.header") ||
		strings.Contains(err.Error(), key) {
		t.Errorf("a bearer push: Resolve err %s; want ErrNoLastKnownGood naming credential.secret.header, without the key",
			strings.ReplaceAll(fmt.Sprint(err), key, "<key>"))
	}
	if strings.Contains(logs.String(), key) {
		t.Fatal("the gateway log carries the key")
	}
}
