package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// keyedUpstream answers by the provider key it is called with, so one
// upstream can serve several orgs' own keys. Keys in failing get a 503.
type keyedUpstream struct {
	mu      sync.Mutex
	calls   map[string]int
	failing map[string]bool
}

func newKeyedUpstream(t *testing.T, failing ...string) (*keyedUpstream, *httptest.Server) {
	t.Helper()
	k := &keyedUpstream{calls: map[string]int{}, failing: map[string]bool{}}
	for _, key := range failing {
		k.failing[key] = true
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		k.mu.Lock()
		k.calls[key]++
		fail := k.failing[key]
		k.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if fail {
			w.WriteHeader(http.StatusServiceUnavailable)
			io.WriteString(w, `{"error":{"message":"synthetic upstream unavailable","type":"server_error"}}`)
			return
		}
		io.WriteString(w, `{"id":"synthetic-completion","object":"chat.completion","model":"adaptive-sample","choices":[{"index":0,"message":{"role":"assistant","content":"synthetic response"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(srv.Close)
	return k, srv
}

func (k *keyedUpstream) count(key string) int {
	k.mu.Lock()
	defer k.mu.Unlock()
	return k.calls[key]
}

// Two orgs bring their own key for "primary"; "backup" is operator-only.
// Requests are locked to primary first and fail over to backup.
func orgBreakerGateway(t *testing.T, failing ...string) (*Server, *httptest.Server, *keyedUpstream) {
	t.Helper()
	keyed, primary := newKeyedUpstream(t, failing...)
	var backupCalls atomic.Int64
	backup := adaptiveUpstream(t, false, 0, &backupCalls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"primary": {BaseURL: primary.URL, APIKey: "synthetic-operator-key", APIFormat: "openai", Models: []string{"adaptive-sample"}},
		"backup":  {BaseURL: backup.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets:         map[string][]config.RoutingTargetConfig{"adaptive-sample": {{Provider: "primary"}, {Provider: "backup"}}},
		Failover:        config.FailoverConfig{Enabled: true, MaxAttempts: 2},
		CircuitBreaker:  config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2, Cooldown: time.Hour},
		ProviderLock:    config.ProviderLockConfig{Enabled: true},
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org-a", Key: "synthetic-org-a-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "org-b", Key: "synthetic-org-b-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "org-b"}},
		{Name: "operator", Key: "synthetic-operator-probe", KeyType: "internal"},
	}
	srv, gateway := adaptiveServer(t, cfg)
	store := tenant.NewStore()
	for _, org := range []string{"org-a", "org-b"} {
		store.Set(org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"primary": {Enabled: true, APIKey: "synthetic-" + org + "-key", Models: []string{"different-model"}},
		}})
	}
	orgCache := providers.NewOrgProviderCache(cfg.Providers, true)
	t.Cleanup(orgCache.EvictAll)
	srv.handlers.tenantStore, srv.handlers.orgProviderCache = store, orgCache
	srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
	return srv, gateway, keyed
}

func sendOrgRequests(t *testing.T, gateway *httptest.Server, probe string, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		if status, body := adaptiveHTTP(t, gateway, "adaptive-sample", probe, false, http.Header{"X-Agentcc-Provider-Lock": {"primary"}}); status != http.StatusOK || !strings.Contains(body, "synthetic response") {
			t.Fatalf("%s request %d: status=%d body=%s", probe, i, status, body)
		}
	}
}

func TestOrgBreakerOverHTTP(t *testing.T) {
	t.Run("one org's failing key opens only its breaker", func(t *testing.T) {
		srv, gateway, keyed := orgBreakerGateway(t, "synthetic-org-a-key")
		sendOrgRequests(t, gateway, "synthetic-org-a-probe", 12)
		if got := keyed.count("synthetic-org-a-key"); got != 2 {
			t.Fatalf("org-a's own key was called %d times; its breaker should open after 2 failures", got)
		}
		sendOrgRequests(t, gateway, "synthetic-org-b-probe", 12)
		if got := keyed.count("synthetic-org-b-key"); got < 3 {
			t.Fatalf("org-b's key was called %d times after org-a's breaker opened", got)
		}
		sendOrgRequests(t, gateway, "synthetic-operator-probe", 4)
		if got := keyed.count("synthetic-operator-key"); got == 0 {
			t.Fatal("org-a's failures stopped operator traffic to primary")
		}
		health := srv.handlers.healthMonitor.GetHealth("primary")
		if health.CircuitState != "closed" || !health.Healthy || health.ErrorCount != 0 {
			t.Fatalf("org-a's key changed shared health: circuit=%s healthy=%t errors=%d", health.CircuitState, health.Healthy, health.ErrorCount)
		}
	})
	t.Run("provider-wide outage opens every org's breaker", func(t *testing.T) {
		_, gateway, keyed := orgBreakerGateway(t, "synthetic-org-a-key", "synthetic-org-b-key")
		for _, org := range []string{"org-a", "org-b"} {
			sendOrgRequests(t, gateway, fmt.Sprintf("synthetic-%s-probe", org), 12)
			if got := keyed.count(fmt.Sprintf("synthetic-%s-key", org)); got != 2 {
				t.Fatalf("%s burned %d attempts on a down provider; want 2", org, got)
			}
		}
	})
}
